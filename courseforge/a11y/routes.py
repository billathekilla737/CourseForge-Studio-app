"""Routes for the HTML remediation: `/api/a11y/{cid}/html/...` and
`/api/batch/a11y`.

The gateway on the page walks five steps: List, Scan, Review, Dry run, Apply.
Here that is `list` (titles only), `fetch` (every body, plus the hard a11y
scan), `transform` (restyle + verify with the chosen look), `push` with
`apply: false` (the plan, nothing sent) and `push` with `apply: true` (the
job that writes, after the confirm gate). `restore` is the undo. The two
sweeps, `bold-structure` and `bordered-boxes`, follow the same dry-run then
confirm pattern.

Every Canvas write goes through `req.app._gate` and lands in the ledger. Only
`app.content` is used; it cannot reach student data.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .. import htmlclean, ledger
from ..routing import HTTPError, route
from . import batch, bold_structure, bordered_boxes, dump, push, restyle
from .workdir import (LOOKS, course_label, load_fixes, load_listing,
                      load_manifest, load_push_result, load_report, read_text,
                      save_fixes, workdir)

PREFIX = "/api/a11y/{cid}/html"
AREA = "a11y"

_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()


def install(app) -> None:
    restyle.configure(getattr(app.cfg, "brand_path", "") or None)
    app.a11y = {"busy": _BUSY}


# ------------------------------------------------------------------ helpers

def _wd(app, cid):
    return workdir(app.course_dir(cid))


def _label(app, cid, manifest=None) -> str:
    return (manifest or {}).get("course_label") or course_label(app, cid)


def _base_url(app) -> str:
    return getattr(app.cfg, "base_url", "") or ""


def _claim(cid: str) -> None:
    with _BUSY_LOCK:
        if cid in _BUSY:
            raise HTTPError(409, "Another accessibility job is still running for this course. "
                                 "Wait for it to finish, then try again.")
        _BUSY.add(cid)


def _release(cid: str) -> None:
    with _BUSY_LOCK:
        _BUSY.discard(cid)


def _locked_job(req, cid: str, kind: str, fn):
    """A job that holds the course's a11y folder while it runs, so two fetches
    or a fetch and a push cannot interleave on the same files."""
    _claim(cid)

    def job(log):
        try:
            return fn(log)
        finally:
            _release(cid)
    return req.job(kind, job)


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return iso
    secs = max(0, int((datetime.now(timezone.utc) - when).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 3600:
        return "%d minutes ago" % (secs // 60)
    if secs < 86400 * 2:
        return "%d hours ago" % (secs // 3600)
    return "%d days ago" % (secs // 86400)


def _strip_bodies(plan: dict) -> dict:
    """The plan for the page, without the full HTML of every item."""
    out = dict(plan)
    for field in ("items", "skipped"):
        if field in out:
            out[field] = [{k: v for k, v in it.items() if k not in ("before", "after")}
                          for it in out[field]]
    return out


# -------------------------------------------------------------------- state

def build_state(app, cid: str) -> dict:
    wd = _wd(app, cid)
    manifest = load_manifest(wd)
    listing = load_listing(wd)
    report = load_report(wd) or []
    fixes = load_fixes(wd)
    last_push = load_push_result(wd)
    excluded = set(fixes.get("excluded") or [])
    by_file = {str(r.get("styled_file")): r for r in report if r.get("styled_file")}
    pushed_keys: set[str] = set()
    if (last_push and manifest and last_push.get("dumped_at") == manifest.get("dumped_at")
            and not last_push.get("restored_at")):
        pushed_keys = set(last_push.get("keys") or [])
    with _BUSY_LOCK:
        busy = cid in _BUSY

    items: dict[str, dict] = {}
    for row in (listing or {}).get("items", []):
        items[row["key"]] = {
            "key": row["key"], "kind": row["kind"], "id": row.get("id"),
            "title": row.get("title") or "", "url": row.get("url"),
            "published": row.get("published"), "modified": row.get("updated_at"),
            "size": row.get("chars"), "fetched": False, "state": "not fetched",
            "issues": [], "verify": None, "excluded": row["key"] in excluded, "pushed": False,
        }
    for it in (manifest or {}).get("items", []):
        key = restyle.item_key(it)
        rec = by_file.get(str(it.get("styled_file"))) if it.get("styled_file") else None
        verify = None
        if rec:
            issues = list(rec.get("issues") or [])
            joined = " ".join(issues)
            verify = {"ok": bool(rec.get("ok")), "issues": issues,
                      "checks": {"text": ("TEXT" not in joined and "headings changed" not in joined),
                                 "links": "href set changed" not in issues,
                                 "images": "src set changed" not in issues},
                      "a11y_after": rec.get("a11y_after") or []}
        is_excluded = key in excluded
        if is_excluded:
            state = "excluded"
        elif verify and not verify["ok"]:
            state = "failed"
        elif key in pushed_keys:
            state = "pushed"
        elif verify and verify["ok"]:
            state = "verified"
        elif it.get("transform_note") == "skipped-empty":
            state = "empty"
        elif it.get("issues"):
            state = "issues"
        else:
            state = "fetched"
        items[key] = {
            "key": key, "kind": it["kind"], "id": it.get("id"),
            "title": it.get("title") or it.get("name") or "", "url": it.get("url"),
            "published": it.get("published"), "modified": it.get("dumped_at"),
            "size": it.get("chars"), "fetched": True, "state": state,
            "issues": list(it.get("issues") or []), "transform_note": it.get("transform_note"),
            "fills_added": it.get("fills_added"), "styled": bool(it.get("styled_file")),
            "verify": verify, "excluded": is_excluded, "pushed": key in pushed_keys,
            "pushed_at": it.get("pushed_at"),
        }
    rows = list(items.values())
    counts = {
        "items": len(rows),
        "fetched": sum(1 for r in rows if r["fetched"]),
        "issues": sum(len(r["issues"]) for r in rows),
        "with_issues": sum(1 for r in rows if r["issues"]),
        "styled": sum(1 for r in rows if r.get("styled")),
        "verified": sum(1 for r in rows if r["verify"] and r["verify"]["ok"]),
        "failed": sum(1 for r in rows if r["verify"] and not r["verify"]["ok"]),
        "excluded": sum(1 for r in rows if r["excluded"]),
        "pushed": len(pushed_keys),
    }
    look = (manifest or {}).get("look") or getattr(app.cfg, "a11y_look", "clean") or "clean"
    return {
        "course_id": str(cid), "kind": "html",
        "course_label": _label(app, cid, manifest), "base_url": _base_url(app),
        "look": look, "default_look": getattr(app.cfg, "a11y_look", "clean") or "clean",
        "looks": list(LOOKS),
        "fetched": bool(manifest), "listed": bool(listing), "busy": busy,
        "dumped_at": (manifest or {}).get("dumped_at"),
        "transformed_at": (manifest or {}).get("transformed_at"),
        "verified_at": (manifest or {}).get("verified_at"),
        "verify_fails": (manifest or {}).get("verify_fails"),
        "verify_ok": bool(manifest) and (manifest or {}).get("verify_fails") == 0 and bool(report),
        "pushed_at": (last_push or {}).get("pushed_at") if pushed_keys else None,
        "restored_at": (manifest or {}).get("restored_at"),
        "last_push": ({k: v for k, v in last_push.items() if k not in ("written", "live", "skipped", "errors")}
                      if last_push else None),
        "counts": counts, "items": rows, "fixes": fixes, "needs": [],
        "summary": _summary_sentence(manifest, counts, look, pushed_keys),
    }


def _summary_sentence(manifest, counts, look, pushed_keys) -> str:
    if not manifest:
        return ("Nothing fetched yet. Fetch reads every page, assignment, discussion and "
                "quiz body plus the syllabus into this folder. Nothing is pushed.")
    n = counts["fetched"]
    parts = ["%d items fetched %s" % (n, _ago(manifest.get("dumped_at")))]
    if counts["with_issues"]:
        parts.append("%d with hard accessibility issues" % counts["with_issues"])
    if manifest.get("verified_at"):
        parts.append("%d verified (%s look)%s" % (
            counts["verified"], look,
            ", %d failing" % counts["failed"] if counts["failed"] else ""))
    if pushed_keys:
        parts.append("%d pushed" % len(pushed_keys))
    return ", ".join(parts) + "."


@route("GET", PREFIX + "/state", AREA)
def state(req):
    return build_state(req.app, req.params["cid"])


# ------------------------------------------------------------- list, fetch

@route("POST", PREFIX + "/list", AREA)
def list_(req):
    cid = req.params["cid"]
    app = req.app

    def job(log):
        log("listing pages, assignments, discussions and quizzes", 0, 4)
        dump.list_items(app.content, cid, _wd(app, cid), log)
        return build_state(app, cid)
    return _locked_job(req, cid, "a11y.list", job)


@route("POST", PREFIX + "/fetch", AREA)
def fetch(req):
    cid = req.params["cid"]
    app = req.app

    def job(log):
        log("fetching every HTML body (pages need one request each)")
        manifest = dump.dump(app.content, cid, _wd(app, cid), log,
                             course_label=_label(app, cid), base_url=_base_url(app))
        issues = sum(len(it.get("issues") or []) for it in manifest["items"])
        log("fetched %d items; %d hard accessibility issue(s) found. Nothing is pushed."
            % (len(manifest["items"]), issues))
        st = build_state(app, cid)
        return {"fetched": len(manifest["items"]), "issues": issues, "state": st}
    return _locked_job(req, cid, "a11y.fetch", job)


# --------------------------------------------------------------- transform

@route("POST", PREFIX + "/transform", AREA)
def transform(req):
    cid = req.params["cid"]
    app = req.app
    wd = _wd(app, cid)
    manifest = load_manifest(wd)
    if not manifest:
        raise HTTPError(400, "Nothing has been fetched for this course yet. Fetch first.")
    look = (req.body.get("look") or manifest.get("look")
            or getattr(app.cfg, "a11y_look", "clean") or "clean")
    if look not in LOOKS:
        raise HTTPError(400, "Unknown look %r. Choose clean, hybrid or rich." % (look,))

    def job(log):
        log("restyling %d items with the %s look" % (len(manifest["items"]), look), 0, 2)
        summary = restyle.transform(wd, look)
        log("verifying: visible text, links, images, ASCII, fills", 1, 2)
        report = restyle.verify(wd, log=lambda line: log(line))
        log("verify: %d item(s), %d failed. Nothing is pushed."
            % (report["count"], report["fails"]), 2, 2)
        return {"transform": summary,
                "verify": {k: v for k, v in report.items() if k != "items"},
                "state": build_state(app, cid)}
    return _locked_job(req, cid, "a11y.transform", job)


# ------------------------------------------------------------------- fixes

@route("POST", PREFIX + "/fixes", AREA)
def fixes(req):
    cid = req.params["cid"]
    wd = _wd(req.app, cid)
    current = load_fixes(wd)
    body = req.body or {}
    if "excluded" in body and isinstance(body["excluded"], list):
        current["excluded"] = sorted({str(k) for k in body["excluded"]})
    elif body.get("key"):
        keys = set(current.get("excluded") or [])
        if body.get("excluded", True):
            keys.add(str(body["key"]))
        else:
            keys.discard(str(body["key"]))
        current["excluded"] = sorted(keys)
    for extra in ("notes",):
        if extra in body:
            current[extra] = body[extra]
    save_fixes(wd, current)
    return build_state(req.app, cid)


# -------------------------------------------------------------------- item

@route("GET", PREFIX + "/item/{key}", AREA)
def item(req):
    cid = req.params["cid"]
    key = req.params["key"]
    which = req.q("which", "before")
    wd = _wd(req.app, cid)
    manifest = load_manifest(wd)
    if not manifest:
        raise HTTPError(404, "Nothing has been fetched for this course yet.")
    found = next((it for it in manifest["items"] if restyle.item_key(it) == key), None)
    if not found:
        raise HTTPError(404, "No fetched item with that key.")
    if which == "after":
        if not found.get("styled_file"):
            raise HTTPError(404, "This item has no restyled version (empty original, or not restyled yet).")
        raw = read_text(wd, found["styled_file"])
    else:
        raw = read_text(wd, found["file"])
    return {"key": key, "which": which, "kind": found["kind"],
            "title": found.get("title") or found.get("name"), "url": found.get("url"),
            "chars": len(raw), "issues": restyle.a11y_issues(raw),
            "html": htmlclean.clean(raw, _base_url(req.app))}


# -------------------------------------------------------------------- push

@route("POST", PREFIX + "/push", AREA)
def push_(req):
    cid = req.params["cid"]
    app = req.app
    body = req.body or {}
    wd = _wd(app, cid)
    kinds = body.get("kinds") or None
    exclude = load_fixes(wd).get("excluded") or []
    try:
        p = push.plan(wd, kinds=kinds, exclude=exclude)
    except push.PushRefused as exc:
        raise HTTPError(409, str(exc))
    if not body.get("apply"):
        return {"dry_run": True, "plan": p,
                "sentence": push.confirm_sentence(p, _label(app, cid, load_manifest(wd)))}
    if not p["rows"]:
        raise HTTPError(409, "Nothing to push: every restyled body is excluded, unchanged or empty.")

    def job(log):
        log("checking the verify report against the styled files", 0, 3)
        plan_now = push.plan(wd, kinds=kinds, exclude=load_fixes(wd).get("excluded") or [])
        manifest = load_manifest(wd)
        label = _label(app, cid, manifest)
        app._gate("a11y.push",
                  {"course_id": str(cid), "keys": sorted(r["key"] for r in plan_now["rows"]),
                   "look": plan_now.get("look")},
                  push.confirm_sentence(plan_now, label), req.confirm,
                  detail=push.confirm_detail(plan_now), what="restyling course content")
        log("writing %s" % plan_now["phrase"], 1, 3)
        result = push.push(app.content, cid, wd, log, kinds=kinds,
                           exclude=load_fixes(wd).get("excluded") or [])
        if result["written_count"]:
            ledger.record(app.course_dir(cid), AREA,
                          "Replaced the bodies of %s with restyled versions (%s look)"
                          % (result["phrase"], result["look"]),
                          url="%s/courses/%s/pages" % (_base_url(app), cid),
                          count=result["written_count"], kind="a11y.push",
                          undo={"route": "/a11y/%s/html/restore" % cid, "body": {}})
        log("done: %d written, %d error(s), %d live check(s) failed"
            % (result["written_count"], len(result["errors"]), result["live_fails"]), 3, 3)
        result["state"] = build_state(app, cid)
        return result
    return _locked_job(req, cid, "a11y.push", job)


@route("POST", PREFIX + "/restore", AREA)
def restore(req):
    cid = req.params["cid"]
    app = req.app
    wd = _wd(app, cid)
    try:
        p = push.restore_plan(wd)
    except push.PushRefused as exc:
        raise HTTPError(409, str(exc))
    if not (req.body or {}).get("apply", True):
        return {"dry_run": True, "plan": p}
    if not p["rows"]:
        raise HTTPError(409, "Nothing to restore: no usable originals from the last push.")

    def job(log):
        label = _label(app, cid, load_manifest(wd))
        app._gate("a11y.restore",
                  {"course_id": str(cid), "keys": sorted(r["key"] for r in p["rows"]),
                   "pushed_at": p.get("pushed_at")},
                  push.restore_sentence(p, label), req.confirm,
                  detail=json.dumps([{"label": r["label"], "from": r["from"], "to": r["to"]}
                                     for r in p["rows"]]),
                  what="restoring course content")
        log("putting back %s" % p["phrase"], 0, len(p["rows"]))
        result = push.restore(app.content, cid, wd, log)
        if result["written_count"]:
            ledger.record(app.course_dir(cid), AREA,
                          "Put back the previous bodies of %s (undo of the restyle pushed %s)"
                          % (result["phrase"], (p.get("pushed_at") or "")[:16].replace("T", " ")),
                          url="%s/courses/%s/pages" % (_base_url(app), cid),
                          count=result["written_count"], kind="a11y.restore")
        result["state"] = build_state(app, cid)
        return result
    return _locked_job(req, cid, "a11y.restore", job)


# ----------------------------------------------------------------- sweeps

@route("POST", PREFIX + "/bold-structure", AREA)
def bold(req):
    cid = req.params["cid"]
    app = req.app
    body = req.body or {}
    options = bold_structure.normalise_options(body.get("options") or {})
    apply = bool(body.get("apply"))

    def job(log):
        log("reading every body and classifying wholly-bold paragraphs")
        plan = bold_structure.scan_course(app.content, cid, options, log)
        log("%d hit(s): %s" % (plan["hit_count"], ", ".join(
            "%d %s" % (plan["counts"][c], c) for c in bold_structure.CLASSES)))
        if not apply or plan["mode"] == "report" or not plan["change_count"]:
            out = _strip_bodies(plan)
            out["dry_run"] = True
            return out
        label = _label(app, cid, load_manifest(_wd(app, cid)))
        app._gate("a11y.bold-structure",
                  {"course_id": str(cid), "keys": sorted(c["key"] for c in plan["changes"]),
                   "options": {k: options[k] for k in ("promote_labels", "unbold_sentences",
                                                       "convert_code_runs", "labels", "sentence_min")}},
                  bold_structure.sentence(plan, label), req.confirm,
                  detail=json.dumps([{"label": c["label"], "from": c["from"], "to": c["to"]}
                                     for c in plan["changes"]]),
                  what="fixing bold used as structure")
        result = bold_structure.apply_plan(app.content, cid, plan, log)
        if result["written_count"]:
            ledger.record(app.course_dir(cid), AREA,
                          "Changed the markup of %d items so bold is no longer used as "
                          "structure; visible text identical" % result["written_count"],
                          url="%s/courses/%s/pages" % (_base_url(app), cid),
                          count=result["written_count"], kind="a11y.bold-structure")
        out = _strip_bodies(plan)
        out.update(result)
        out["dry_run"] = False
        return out
    return _locked_job(req, cid, "a11y.bold-structure", job)


@route("POST", PREFIX + "/bordered-boxes", AREA)
def boxes(req):
    cid = req.params["cid"]
    app = req.app
    body = req.body or {}
    color = (body.get("color") or "").strip() or None
    apply = bool(body.get("apply"))

    def job(log):
        log("reading every body, including announcements and quiz questions")
        plan = bordered_boxes.scan_course(app.content, cid, color, log)
        log("%d bordered-box span(s) in %d item(s)" % (plan["box_count"], plan["change_count"]))
        if not apply or not plan["change_count"]:
            out = _strip_bodies(plan)
            out["dry_run"] = True
            return out
        label = _label(app, cid, load_manifest(_wd(app, cid)))
        app._gate("a11y.bordered-boxes",
                  {"course_id": str(cid), "keys": sorted(c["key"] for c in plan["changes"]),
                   "color": plan["color"]},
                  bordered_boxes.sentence(plan, label), req.confirm,
                  detail=json.dumps([{"label": c["label"], "from": c["from"], "to": c["to"]}
                                     for c in plan["changes"]]),
                  what="removing bordered-box emphasis")
        result = bordered_boxes.apply_plan(app.content, cid, plan, log)
        if result["written_count"]:
            ledger.record(app.course_dir(cid), AREA,
                          "Removed %d inline bordered-box spans from %d items; text unchanged"
                          % (result["boxes_removed"], result["written_count"]),
                          url="%s/courses/%s/pages" % (_base_url(app), cid),
                          count=result["written_count"], kind="a11y.bordered-boxes")
        out = _strip_bodies(plan)
        out.update(result)
        out["dry_run"] = False
        return out
    return _locked_job(req, cid, "a11y.bordered-boxes", job)


# ------------------------------------------------------------------- batch

@route("GET", "/api/batch/a11y", AREA)
def batch_last(req):
    return batch.last_summary(req.app) or {"rows": [], "course_ids": [], "look": None}


@route("POST", "/api/batch/a11y", AREA)
def batch_run(req):
    app = req.app
    body = req.body or {}
    course_ids = [str(c).strip() for c in (body.get("course_ids") or []) if str(c).strip()]
    if not course_ids:
        raise HTTPError(400, "Pick at least one course.")
    look = body.get("look") or getattr(app.cfg, "a11y_look", "clean") or "clean"
    if look not in LOOKS:
        raise HTTPError(400, "Unknown look %r. Choose clean, hybrid or rich." % (look,))
    apply = bool(body.get("apply"))

    def gate(payload, sentence, detail):
        app._gate("a11y.batch", payload, sentence, req.confirm, detail=detail,
                  what="batch restyling")

    def job(log):
        return batch.run(app, course_ids, look=look, apply=apply, log=log,
                         gate=gate if apply else None)
    return req.job("a11y.batch", job)


# --------------------------------------------------------------- hub card

def hub_status(app, course_id) -> dict:
    """Local state only; the hub paints in tens of milliseconds."""
    href = "#/c/%s/a11y/html" % course_id
    wd = _wd(app, course_id)
    m = load_manifest(wd)
    if not m:
        return {"lines": ["HTML: not fetched yet. Fetch reads every page body into this "
                          "folder; nothing is pushed."],
                "badge": None, "needs": [], "actions": [{"label": "Fix HTML", "href": href}]}
    items = m.get("items", [])
    line = "HTML: %d items" % len(items)
    fails = m.get("verify_fails")
    if m.get("verified_at"):
        line += ", verified %s (%s look)" % (_ago(m["verified_at"]), m.get("look") or "clean")
        if fails:
            line += ", %d failing" % fails
    elif m.get("look"):
        line += ", restyled (%s look), not verified" % m["look"]
    else:
        line += ", fetched %s" % _ago(m.get("dumped_at"))
    last_push = load_push_result(wd)
    if last_push and last_push.get("dumped_at") == m.get("dumped_at"):
        if last_push.get("restored_at"):
            line += ", restored"
        else:
            line += ", pushed"
    issues = sum(1 for it in items if it.get("issues"))
    if issues and not m.get("verified_at"):
        line += "; %d with hard issues" % issues
    return {"lines": [line + "."], "badge": (fails or None), "needs": [],
            "actions": [{"label": "Fix HTML", "href": href}]}
