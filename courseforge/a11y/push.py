"""Step 3 of the HTML remediation: put the restyled bodies back in place, and
the undo (a port of Push-CanvasRemediation.ps1).

    page       -> PUT /pages/:slug           wiki_page[body]
    assignment -> PUT /assignments/:id       assignment[description]
    discussion -> PUT /discussion_topics/:id message
    quiz       -> PUT /quizzes/:id           quiz[description]   (JSON; form is ignored)
    syllabus   -> PUT /courses/:id           course[syllabus_body]

The client's `write_content_body` picks the encoding per kind.

Gates, each learned the hard way:
  - Nothing but the body field is ever touched: not modules, publish state,
    titles.
  - `plan` is the dry run. Nothing here writes unless `push` is called, and
    the route puts the confirm gate between the two.
  - The verify report must exist, be a passing one, and describe the exact
    files about to be pushed (sha256 per styled file). A stale report used to
    let never-verified bodies into a live course; now it refuses.
  - Empty-body guard: a styled body under MIN_BODY characters is never written
    (an empty PUT returns 200 and wipes the item).
  - Before each write the live body is read and compared with the fetched
    original. An item edited in Canvas since the fetch is skipped, not
    clobbered.
  - The first write that answers 403 aborts the run: the course is
    write-locked (concluded, closed grading period). The client already
    retries rate-limit 403s, so one that surfaces here is not a throttle.
  - Live re-verify: every written item is fetched back and must be non-empty,
    carry the navy fill when one was added, and read the same visible text.
  - The originals of everything written are kept under pushed/<stamp>/, and
    `restore` writes them back with the same guards.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from . import restyle
from .workdir import (describe_counts, fmt_size, kind_label, load_fixes,
                      load_manifest, load_push_result, load_report, now_iso,
                      read_text, save_manifest, save_push_result,
                      save_restore_result, stamp, strip_theme, write_text)

MIN_BODY = 20
KINDS = ("page", "assignment", "discussion", "quiz", "syllabus")


class PushRefused(Exception):
    """A gate said no. The message is a sentence for a person."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _navy():
    return re.compile(re.escape(restyle.NAVY), re.I)


def _status(exc) -> int | None:
    status = getattr(exc, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ gates

def check_report(wd, manifest: dict) -> dict:
    """The verify report must be passing and match the styled files on disk.
    Returns the report keyed by styled_file, or raises PushRefused."""
    report = load_report(wd)
    if report is None:
        raise PushRefused("There is no verify report for this course yet. Restyle and "
                          "verify first; nothing is pushed without a passing verify.")
    failed = [r for r in report if not r.get("ok")]
    if failed:
        names = ", ".join((r.get("name") or str(r.get("id")))[:40] for r in failed[:5])
        raise PushRefused("The verify report has %d failing item(s) (%s%s). Fix them or "
                          "exclude them, then verify again before pushing."
                          % (len(failed), names, ", ..." if len(failed) > 5 else ""))
    by_file = {str(r["styled_file"]): r for r in report if r.get("styled_file")}
    if not by_file:
        raise PushRefused("The verify report carries no file digests. Verify again.")
    stale = []
    for it in manifest.get("items", []):
        sf = it.get("styled_file")
        if not sf:
            continue
        rec = by_file.get(str(sf))
        title = it.get("title") or it.get("name") or str(it.get("id"))
        if not rec:
            stale.append("%s (not in the verify report)" % title)
            continue
        path = Path(restyle.resolve_path(wd, sf))
        if not path.is_file():
            stale.append("%s (styled file missing)" % title)
            continue
        if _sha(read_text(wd, sf)) != str(rec.get("styled_sha256")):
            stale.append("%s (changed since verify)" % title)
    if stale:
        shown = "; ".join(stale[:8]) + (" ..." if len(stale) > 8 else "")
        raise PushRefused("The verify report is stale: %d styled file(s) changed after it "
                          "was written (%s). Verify again before pushing." % (len(stale), shown))
    return by_file


# ------------------------------------------------------------------- plan

def plan(wd, kinds=None, exclude=None) -> dict:
    """The dry run. Which styled bodies would be written, and which would not,
    and why. Nothing is sent."""
    wd = Path(wd)
    m = load_manifest(wd)
    if not m or not m.get("items"):
        raise PushRefused("Nothing has been fetched for this course yet. Fetch first.")
    check_report(wd, m)
    kinds = set(k.lower() for k in kinds) if kinds else None
    exclude = set(exclude or [])
    rows, skipped = [], []
    for it in m["items"]:
        key = restyle.item_key(it)
        title = it.get("title") or it.get("name") or str(it.get("id"))
        base = {"key": key, "kind": it["kind"], "id": it.get("id"), "title": title,
                "label": "%s: %s" % (kind_label(it["kind"]), title)}
        if not it.get("styled_file"):
            skipped.append(dict(base, reason="empty original; nothing to restyle"))
            continue
        if kinds and it["kind"] not in kinds:
            skipped.append(dict(base, reason="kind not selected"))
            continue
        if key in exclude:
            skipped.append(dict(base, reason="excluded by you"))
            continue
        styled = read_text(wd, it["styled_file"])
        original = read_text(wd, it["file"])
        if len(styled.strip()) < MIN_BODY:
            skipped.append(dict(base, reason="styled body is empty; writing it would wipe the item"))
            continue
        if styled == original:
            skipped.append(dict(base, reason="already matches; no change"))
            continue
        rows.append(dict(base, **{"from": fmt_size(len(original)), "to": fmt_size(len(styled)),
                                  "from_chars": len(original), "to_chars": len(styled),
                                  "fills_added": int(it.get("fills_added") or 0),
                                  "published": it.get("published"),
                                  "transform_note": it.get("transform_note")}))
    counts = Counter(r["kind"] for r in rows)
    return {"course_id": m.get("course_id"), "course_label": m.get("course_label"),
            "look": m.get("look"), "verified_at": m.get("verified_at"),
            "rows": rows, "skipped": skipped, "count": len(rows),
            "counts": dict(counts), "phrase": describe_counts(counts)}


def confirm_sentence(p: dict, course_label: str) -> str:
    return ("Replace the bodies of %s in %s with the restyled versions (%s look). "
            "Visible text is verified unchanged. Modules and publish state are not "
            "touched. The previous bodies are kept here and can be put back with Restore."
            % (p["phrase"], course_label, p.get("look") or "clean"))


def confirm_detail(p: dict) -> str:
    return json.dumps([{"label": r["label"], "from": r["from"], "to": r["to"]}
                       for r in p["rows"]])


# ------------------------------------------------------------------- push

def _log(log, message, done=None, total=None):
    if log:
        try:
            log(message, done, total)
        except TypeError:
            log(message)


def push(client, course_id, wd, log=None, kinds=None, exclude=None) -> dict:
    """Write the planned bodies. Call `plan` first and put the confirm gate in
    between; this re-runs every gate itself right before the first write."""
    wd = Path(wd)
    p = plan(wd, kinds=kinds, exclude=exclude)
    m = load_manifest(wd)
    by_key = {restyle.item_key(it): it for it in m["items"]}
    look = m.get("look") or "clean"
    if not p["rows"]:
        raise PushRefused("Nothing to push: every restyled body is excluded, unchanged or empty.")

    written, errors, skipped = [], [], list(p["skipped"])
    total = len(p["rows"])
    first = True
    for i, row in enumerate(p["rows"], 1):
        it = by_key[row["key"]]
        body = read_text(wd, it["styled_file"])
        if len(body.strip()) < MIN_BODY:                 # belt and braces
            errors.append(dict(row, error="styled body empty; skipped so the item is not wiped"))
            continue
        # Do not clobber an edit made in Canvas since the fetch.
        try:
            live_now = strip_theme(client.read_content_body(course_id, it["kind"], it["id"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(dict(row, error="could not read the live body first: %s" % str(exc)[:200]))
            continue
        original = read_text(wd, it["file"])
        if restyle.visible_text(live_now) != restyle.visible_text(original):
            skipped.append(dict(row, reason="changed in Canvas since it was fetched; fetch again"))
            _log(log, "skipped %s: changed in Canvas since the fetch" % row["label"], i, total)
            continue
        try:
            client.write_content_body(course_id, it["kind"], it["id"], body)
        except Exception as exc:  # noqa: BLE001
            status = _status(exc)
            if first and status == 403:
                raise PushRefused(
                    "The first write (%s) was refused with 403 and it is not a rate limit, "
                    "so the course is probably write-locked: concluded, or in a closed "
                    "grading period. Nothing has been changed. Ask the instructor or the "
                    "registrar to re-open it." % row["label"]) from exc
            first = False
            errors.append(dict(row, error=str(exc)[:300], status=status))
            _log(log, "failed %s: %s" % (row["label"], str(exc)[:120]), i, total)
            continue
        first = False
        written.append(row)
        _log(log, "wrote %s (%s -> %s)" % (row["label"], row["from"], row["to"]), i, total)

    # Keep the originals of everything just replaced, so Restore has them
    # even after the next fetch overwrites bodies/.
    snap = wd / "pushed" / stamp()
    originals = []
    for row in written:
        it = by_key[row["key"]]
        src = Path(restyle.resolve_path(wd, it["file"]))
        dest = snap / "bodies" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        originals.append({"key": row["key"], "kind": it["kind"], "id": it["id"],
                          "title": row["title"], "label": row["label"],
                          "file": "bodies/" + src.name, "chars": len(read_text(wd, it["file"]))})
    if written:
        with open(snap / "originals.json", "w", encoding="utf-8") as fh:
            json.dump({"course_id": str(course_id), "pushed_at": now_iso(),
                       "dumped_at": m.get("dumped_at"), "items": originals}, fh, indent=1)

    # Live re-verify: only what was actually written.
    live = []
    navy = _navy()
    for j, row in enumerate(written, 1):
        it = by_key[row["key"]]
        pushed_body = read_text(wd, it["styled_file"])
        rec = {"key": row["key"], "kind": it["kind"], "title": row["title"],
               "label": row["label"], "ok": True, "issues": [], "note": ""}
        try:
            live_body = client.read_content_body(course_id, it["kind"], it["id"]) or ""
        except Exception as exc:  # noqa: BLE001
            rec["issues"].append("could not fetch it back: %s" % str(exc)[:160])
        else:
            rec["chars"] = len(live_body)
            if it["kind"] == "quiz" and it.get("published") is False:
                # An unpublished Classic Quiz serves its last PUBLISHED copy on
                # GET (canvas-api-gotchas 3). The PUT echoed the new body; the
                # read cannot confirm it, so say so instead of failing.
                rec["note"] = ("unpublished quiz: Canvas serves the last published copy on "
                               "read, so the write is trusted from its own response")
            else:
                if len(live_body.strip()) < MIN_BODY:
                    rec["issues"].append("empty on the live course")
                expect_navy = look != "clean" and int(it.get("fills_added") or 0) > 0
                if expect_navy and not navy.search(live_body):
                    rec["issues"].append("navy fill missing")
                if restyle.visible_text(strip_theme(live_body)) != restyle.visible_text(pushed_body):
                    rec["issues"].append("visible text differs on the live course")
        rec["ok"] = not rec["issues"]
        live.append(rec)
        _log(log, "%s %s" % ("verified" if rec["ok"] else "CHECK", row["label"]), j, len(written))

    result = {
        "course_id": str(course_id), "look": look, "pushed_at": now_iso(),
        "dumped_at": m.get("dumped_at"), "verified_at": m.get("verified_at"),
        "written": written, "written_count": len(written),
        "errors": errors, "skipped": skipped,
        "live": live, "live_fails": sum(1 for r in live if not r["ok"]),
        "keys": [r["key"] for r in written],
        "originals_dir": str(snap.relative_to(wd)) if written else None,
        "counts": dict(Counter(r["kind"] for r in written)),
        "phrase": describe_counts(Counter(r["kind"] for r in written)),
    }
    save_push_result(wd, result)
    m["pushed_at"] = result["pushed_at"]
    for row in written:
        by_key[row["key"]]["pushed_at"] = result["pushed_at"]
    save_manifest(wd, m)
    _log(log, "push complete: %d written, %d error(s), %d live check(s) failed"
         % (len(written), len(errors), result["live_fails"]), len(written), len(written))
    return result


# ---------------------------------------------------------------- restore

def restore_plan(wd) -> dict:
    """What Restore would write back: the originals kept by the last push."""
    wd = Path(wd)
    last = load_push_result(wd)
    if not last or not last.get("originals_dir"):
        raise PushRefused("Nothing has been pushed from here, so there is nothing to restore.")
    folder = wd / last["originals_dir"]
    meta_path = folder / "originals.json"
    if not meta_path.is_file():
        raise PushRefused("The originals kept by the last push are missing (%s)." % folder)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    rows, skipped = [], []
    for it in meta.get("items", []):
        path = folder / it["file"]
        if not path.is_file():
            skipped.append(dict(it, reason="original file missing"))
            continue
        text = path.read_text(encoding="utf-8")
        if len(text.strip()) < MIN_BODY:
            skipped.append(dict(it, reason="original is empty; writing it would wipe the item"))
            continue
        rows.append(dict(it, **{"from": "restyled", "to": fmt_size(len(text)),
                                "path": str(path)}))
    counts = Counter(r["kind"] for r in rows)
    return {"course_id": last.get("course_id"), "pushed_at": last.get("pushed_at"),
            "look": last.get("look"), "rows": rows, "skipped": skipped,
            "count": len(rows), "counts": dict(counts), "phrase": describe_counts(counts),
            "restored_at": last.get("restored_at")}


def restore_sentence(p: dict, course_label: str) -> str:
    return ("Put back the previous bodies of %s in %s, undoing the restyle pushed %s. "
            "Modules and publish state are not touched. The restyled versions stay "
            "here and can be pushed again."
            % (p["phrase"], course_label, (p.get("pushed_at") or "")[:16].replace("T", " ")))


def restore(client, course_id, wd, log=None) -> dict:
    wd = Path(wd)
    p = restore_plan(wd)
    if not p["rows"]:
        raise PushRefused("Nothing to restore: no usable originals from the last push.")
    written, errors, live = [], [], []
    first = True
    total = len(p["rows"])
    for i, row in enumerate(p["rows"], 1):
        body = Path(row["path"]).read_text(encoding="utf-8")
        try:
            client.write_content_body(course_id, row["kind"], row["id"], body)
        except Exception as exc:  # noqa: BLE001
            status = _status(exc)
            if first and status == 403:
                raise PushRefused(
                    "The first write (%s) was refused with 403, so the course is probably "
                    "write-locked. Nothing has been changed." % row["label"]) from exc
            first = False
            errors.append(dict(row, error=str(exc)[:300], status=status))
            _log(log, "failed %s: %s" % (row["label"], str(exc)[:120]), i, total)
            continue
        first = False
        written.append(row)
        _log(log, "restored %s" % row["label"], i, total)
    for j, row in enumerate(written, 1):
        body = Path(row["path"]).read_text(encoding="utf-8")
        rec = {"key": row["key"], "kind": row["kind"], "title": row["title"],
               "label": row["label"], "ok": True, "issues": [], "note": ""}
        try:
            live_body = client.read_content_body(course_id, row["kind"], row["id"]) or ""
        except Exception as exc:  # noqa: BLE001
            rec["issues"].append("could not fetch it back: %s" % str(exc)[:160])
        else:
            if len(live_body.strip()) < MIN_BODY:
                rec["issues"].append("empty on the live course")
            elif restyle.visible_text(strip_theme(live_body)) != restyle.visible_text(body):
                rec["issues"].append("visible text differs from the original")
        rec["ok"] = not rec["issues"]
        live.append(rec)
        _log(log, "%s %s" % ("verified" if rec["ok"] else "CHECK", row["label"]), j, len(written))
    result = {"course_id": str(course_id), "restored_at": now_iso(),
              "undoes_push_at": p.get("pushed_at"), "written": written,
              "written_count": len(written), "errors": errors, "live": live,
              "live_fails": sum(1 for r in live if not r["ok"]),
              "counts": dict(Counter(r["kind"] for r in written)),
              "phrase": describe_counts(Counter(r["kind"] for r in written))}
    save_restore_result(wd, result)
    last = load_push_result(wd) or {}
    last["restored_at"] = result["restored_at"]
    save_push_result(wd, last)
    m = load_manifest(wd)
    if m:
        m["restored_at"] = result["restored_at"]
        save_manifest(wd, m)
    return result


def excluded_keys(wd) -> list[str]:
    return list(load_fixes(wd).get("excluded") or [])
