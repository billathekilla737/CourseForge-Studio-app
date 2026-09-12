"""Step 1 of the HTML remediation: fetch every body Ally scans into the work
folder (a port of Dump-CanvasContent.ps1).

Pages need one GET each (the list endpoint carries no bodies). Assignment
shells backed by a quiz or a discussion are skipped by the client
(`iter_content_bodies`): their body lives on the quiz or topic, and a write to
the shell 400s. Discussion messages, quiz descriptions and the syllabus round
it out. The auto-injected account theme <link>/<script> is stripped on save.

Reads CONTENT only. The content-scoped client refuses student endpoints before
a request is sent, so nothing here can reach submissions or people.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from . import restyle
from .workdir import (kind_label, load_manifest, now_iso, save_listing,
                      save_manifest, strip_theme, write_text)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def file_name(kind: str, ident, used: set | None = None) -> str:
    """bodies/<Kind>_<id>.html, filesystem-safe and unique within a dump."""
    base = "%s_%s" % (kind_label(kind), _SAFE.sub("_", str(ident))[:120] or "item")
    name = base + ".html"
    if used is not None:
        n = 2
        while name in used:
            name = "%s-%d.html" % (base, n)
            n += 1
        used.add(name)
    return name


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_items(client, course_id, wd, log=None) -> dict:
    """The cheap listing for the gateway's first step: titles, kinds and
    publish state without fetching page bodies. Four paged calls."""
    wd = Path(wd)
    rows = []
    for p in client.pages(course_id):
        slug = p.get("url")
        if not slug:
            continue
        rows.append({"kind": "page", "id": slug, "key": "page_%s" % slug,
                     "title": p.get("title"), "url": p.get("html_url"),
                     "published": p.get("published"), "updated_at": p.get("updated_at")})
    for a in client.assignments_content(course_id):
        if a.get("quiz_id") or a.get("discussion_topic"):
            continue
        rows.append({"kind": "assignment", "id": a["id"], "key": "assignment_%s" % a["id"],
                     "title": a.get("name"), "url": a.get("html_url"),
                     "published": a.get("published"), "updated_at": a.get("updated_at"),
                     "chars": len(a.get("description") or "")})
    for d in client.discussions(course_id):
        rows.append({"kind": "discussion", "id": d["id"], "key": "discussion_%s" % d["id"],
                     "title": d.get("title"), "url": d.get("html_url"),
                     "published": d.get("published"), "updated_at": d.get("last_reply_at"),
                     "chars": len(d.get("message") or "")})
    for q in client.quizzes_content(course_id):
        rows.append({"kind": "quiz", "id": q["id"], "key": "quiz_%s" % q["id"],
                     "title": q.get("title"), "url": q.get("html_url"),
                     "published": q.get("published"), "updated_at": None,
                     "chars": len(q.get("description") or "")})
    rows.append({"kind": "syllabus", "id": str(course_id), "key": "syllabus",
                 "title": "Syllabus", "url": None, "published": True, "updated_at": None})
    listing = {"course_id": str(course_id), "listed_at": now_iso(), "items": rows}
    save_listing(wd, listing)
    if log:
        log("listed %d items" % len(rows))
    return listing


def dump(client, course_id, wd, log=None, course_label: str = "",
         base_url: str = "") -> dict:
    """Fetch every HTML body into bodies/ and write manifest.json.

    A fresh dump replaces the previous originals and clears the styled files
    and the verify report, since they described bodies that may have changed.
    The originals that an earlier push replaced are kept under pushed/, so a
    re-fetch never loses the way to restore.
    """
    wd = Path(wd)
    bodies = wd / "bodies"
    bodies.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(wd) or {}
    items: list[dict] = []
    used: set[str] = set()
    when = now_iso()
    total = 0
    for entry in client.iter_content_bodies(course_id):
        total += 1
        kind = str(entry.get("kind") or "").lower()
        body = strip_theme(entry.get("body") or "")
        if kind == "syllabus" and not body.strip():
            continue                      # an empty syllabus is not an item
        name = file_name(kind, entry.get("id"), used)
        write_text(bodies / name, body)
        item = {
            "kind": kind,
            "id": entry.get("id"),
            "key": entry.get("key") or "%s_%s" % (kind, entry.get("id")),
            "title": entry.get("title") or "",
            "name": entry.get("title") or "",          # restyle.py reads `name`
            "slug": entry.get("id") if kind == "page" else None,
            "url": entry.get("url"),
            "published": entry.get("published"),
            "front_page": bool(entry.get("front_page")),
            "chars": len(body),
            "sha256": _sha(body),
            "dumped_at": when,
            "file": "bodies/" + name,
            "issues": restyle.a11y_issues(body),
        }
        items.append(item)
        if log:
            log("fetched %s: %s (%d chars)" % (kind_label(kind), item["title"], len(body)),
                len(items), None)
    # Bodies of items that no longer exist would otherwise linger and confuse
    # the review pane; the styled files and the report described old bodies.
    keep = {Path(it["file"]).name for it in items}
    for stale in bodies.glob("*.html"):
        if stale.name not in keep:
            stale.unlink(missing_ok=True)
    shutil.rmtree(wd / "styled", ignore_errors=True)
    (wd / "verify-report.json").unlink(missing_ok=True)
    manifest = {
        "course_id": str(course_id),
        "base_url": base_url or getattr(client, "base", ""),
        "course_label": course_label or previous.get("course_label") or "Course %s" % course_id,
        "dumped_at": when,
        "look": previous.get("look"),
        "items": items,
    }
    save_manifest(wd, manifest)
    if log:
        log("fetched %d items into %s" % (len(items), wd), len(items), len(items))
    return manifest
