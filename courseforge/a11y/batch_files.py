"""ADA file compliance across several courses at once.

The per-course screens fix the files in one course. This drives those same
functions over a list of courses, because "which of my handouts are a problem"
is a question about a teaching load, not about one shell.

It does the work in the same order a person would:

  survey   read-only. What is out there, and what does this machine already
           know about it. Costs a file listing per course and nothing else.
  scan     download a copy of every file and look at it. For PDFs that also
           runs the repair engine, which is local: it writes fixed.pdf beside
           the original and uploads nothing.
  push     put the fixed files back over their originals. The only step that
           changes Canvas, gated once for the whole run.

A course that fails is recorded and the run carries on, because one broken
course should not cost you the other nineteen. Nothing is ever uploaded that
did not pass its own verification, which is enforced in the per-course push and
not re-decided here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..docs import gateway
from ..pdf import course_pdfs
from .workdir import course_label

# The three that carry an accessibility obligation. The find-and-replace kinds
# (pdf-text, office-text) are a different job and are not offered here.
KINDS = ("pdf", "pptx", "docx")
KIND_LABEL = {"pdf": "PDFs", "pptx": "PowerPoint", "docx": "Word"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(log, message, done=None, total=None):
    if log:
        try:
            log(message, done, total)
        except TypeError:
            log(message)


def _data_root(ctx) -> Path:
    root = getattr(getattr(ctx, "store", None), "root", None)
    return Path(root) if root else Path(getattr(ctx.cfg, "data_dir", "data"))


def summary_path(ctx) -> Path:
    return _data_root(ctx) / "batch" / "files-summary.json"


def last_summary(ctx) -> dict | None:
    path = summary_path(ctx)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(ctx, summary: dict) -> dict:
    path = summary_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    summary["path"] = str(path)
    return summary


def _clean_kinds(kinds) -> list[str]:
    out = [k for k in (kinds or KINDS) if k in KINDS]
    if not out:
        raise ValueError("no file kinds given; pick from %s" % ", ".join(KINDS))
    return out


def _clean_courses(course_ids) -> list[str]:
    out = [str(c).strip() for c in (course_ids or []) if str(c).strip()]
    if not out:
        raise ValueError("no course ids given")
    return out


# ------------------------------------------------------------------ survey

def _pdf_row(ctx, cid) -> dict:
    st = course_pdfs.state(ctx, cid)
    files = st.get("files") or []
    return {
        "files": len(files),
        "scanned": sum(1 for f in files if f.get("state") not in ("", "original", None)),
        "needs_person": len(st.get("queue") or []),
        "ready": sum(1 for f in files if f.get("state") in ("fixed", "verified")),
        "uploaded": sum(1 for f in files if f.get("state") == "uploaded"),
        "listed_at": st.get("listed_at"),
        "needs": st.get("needs") or [],
    }


def _docs_row(ctx, cid, kind_id) -> dict:
    kind = gateway.KINDS[kind_id]
    listed = gateway.listed_files(ctx, cid, kind) or {}
    rows = listed.get("files") or listed.get("items") or []
    items = gateway.items(ctx, cid, kind)
    scanned = [it for it in items if getattr(it, "report", None)]
    issues = 0
    for it in scanned:
        try:
            summary = kind.adapter.summary(it.report, it.fixes) or {}
        except Exception:  # noqa: BLE001
            summary = {}
        issues += int(summary.get("issues") or summary.get("problems") or 0)
    return {
        "files": len(rows),
        "scanned": len(scanned),
        "issues": issues,
        "listed_at": listed.get("listed_at"),
        "needs": gateway.tool_needs(ctx, kind),
    }


def survey(ctx, course_ids, kinds=None, log=None) -> dict:
    """What is out there and what this machine already knows. Read-only.

    Deliberately cheap: one file listing per course, then local state. It will
    not tell you a PDF is untagged, because knowing that means downloading it,
    which is what `scan` is for.
    """
    course_ids = _clean_courses(course_ids)
    kinds = _clean_kinds(kinds)
    rows, total = [], len(course_ids)
    for n, cid in enumerate(course_ids, 1):
        row = {"course": cid, "name": "", "kinds": {}, "error": ""}
        rows.append(row)
        try:
            row["name"] = course_label(ctx, cid)
            _log(log, "%s: listing files" % row["name"], n, total)
            for kind_id in kinds:
                if kind_id == "pdf":
                    course_pdfs.list_files(ctx, cid)
                    row["kinds"][kind_id] = _pdf_row(ctx, cid)
                else:
                    gateway.list_files(ctx, cid, gateway.KINDS[kind_id])
                    row["kinds"][kind_id] = _docs_row(ctx, cid, kind_id)
        except Exception as exc:  # noqa: BLE001
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
            _log(log, "%s: %s" % (cid, row["error"]), n, total)
    return {"ran_at": now_iso(), "action": "survey", "kinds": kinds,
            "course_ids": course_ids, "rows": rows, "totals": totals(rows, kinds)}


def totals(rows: list[dict], kinds) -> dict:
    out = {}
    for kind_id in kinds:
        agg = {"files": 0, "scanned": 0, "issues": 0, "needs_person": 0,
               "ready": 0, "uploaded": 0}
        for r in rows:
            k = (r.get("kinds") or {}).get(kind_id) or {}
            for key in agg:
                agg[key] += int(k.get(key) or 0)
        out[kind_id] = agg
    return out


# -------------------------------------------------------------------- scan

def scan(ctx, course_ids, kinds=None, log=None, fix_pdfs: bool = True) -> dict:
    """Download a copy of every file and look at it. Uploads nothing.

    For PDFs this also runs the repair engine, because a PDF's real state is
    only known once it has been through it. The result stays on this machine.
    """
    course_ids = _clean_courses(course_ids)
    kinds = _clean_kinds(kinds)
    rows, total = [], len(course_ids)
    for n, cid in enumerate(course_ids, 1):
        row = {"course": cid, "name": "", "kinds": {}, "error": ""}
        rows.append(row)
        try:
            row["name"] = course_label(ctx, cid)
            for kind_id in kinds:
                label = "%s: %s" % (row["name"], KIND_LABEL[kind_id])
                if kind_id == "pdf":
                    _log(log, "%s: fetching" % label, n, total)
                    course_pdfs.fetch(ctx, cid)
                    if fix_pdfs:
                        _log(log, "%s: repairing" % label, n, total)
                        course_pdfs.fix(ctx, cid)
                    row["kinds"][kind_id] = _pdf_row(ctx, cid)
                else:
                    _log(log, "%s: fetching and scanning" % label, n, total)
                    gateway.fetch(ctx, cid, gateway.KINDS[kind_id])
                    row["kinds"][kind_id] = _docs_row(ctx, cid, kind_id)
        except Exception as exc:  # noqa: BLE001
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
            _log(log, "%s: %s" % (cid, row["error"]), n, total)
    summary = {"ran_at": now_iso(), "action": "scan", "kinds": kinds, "applied": False,
               "course_ids": course_ids, "rows": rows, "totals": totals(rows, kinds)}
    return _save(ctx, summary)


# -------------------------------------------------------------------- push

def push_plan(ctx, course_ids, kinds=None) -> dict:
    """What would be uploaded, per course. Reads local state only."""
    course_ids = _clean_courses(course_ids)
    kinds = _clean_kinds(kinds)
    rows, ready, unverified = [], 0, 0
    for cid in course_ids:
        row = {"course": cid, "name": course_label(ctx, cid), "kinds": {}, "error": ""}
        rows.append(row)
        try:
            for kind_id in kinds:
                # Both dry runs answer with a count and a list of what is held
                # back; only the name of that list differs.
                if kind_id == "pdf":
                    p = course_pdfs.plan(ctx, cid) or {}
                    blocked = len(p.get("held") or [])
                else:
                    p = gateway.push(ctx, cid, gateway.KINDS[kind_id], apply=False) or {}
                    blocked = len(p.get("blocked") or [])
                n = int(p.get("count") or 0)
                row["kinds"][kind_id] = {"ready": n, "blocked": blocked}
                ready += n
                unverified += blocked
        except Exception as exc:  # noqa: BLE001
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
    return {"rows": rows, "ready": ready, "unverified": unverified,
            "course_ids": course_ids, "kinds": kinds}


def push_sentence(plan: dict) -> str:
    n, courses = plan["ready"], len(plan["course_ids"])
    kinds = ", ".join(KIND_LABEL[k] for k in plan["kinds"])
    what = ("1 fixed file over its original" if n == 1
            else "%d fixed files over their originals" % n)
    where = "1 course" if courses == 1 else "%d courses" % courses
    out = ("Upload %s across %s (%s). Names, folders and links stay the same, "
           "and the originals are kept on this computer." % (what, where, kinds))
    if plan["unverified"]:
        out += (" %d file%s that did not pass verification %s not included."
                % (plan["unverified"], "" if plan["unverified"] == 1 else "s",
                   "is" if plan["unverified"] == 1 else "are"))
    return out


def push(ctx, course_ids, kinds=None, apply: bool = False, gate=None, log=None) -> dict:
    """Upload the verified fixed files. One question for the whole run.

    The per-course push decides what is fit to upload; this does not second
    guess it. A course that fails is recorded and the rest carry on.
    """
    plan = push_plan(ctx, course_ids, kinds)
    if not apply:
        return {"ran_at": now_iso(), "action": "push", "applied": False,
                "dry_run": True, "sentence": push_sentence(plan), **plan}
    if gate:
        gate({"course_ids": plan["course_ids"], "kinds": plan["kinds"],
              "ready": plan["ready"]},
             push_sentence(plan),
             [{"label": r["name"],
               "from": "%d ready" % sum(k["ready"] for k in r["kinds"].values()),
               "to": "uploaded"} for r in plan["rows"]])

    rows, total = [], len(plan["course_ids"])
    uploaded = failed = 0
    for n, cid in enumerate(plan["course_ids"], 1):
        row = {"course": cid, "name": course_label(ctx, cid), "kinds": {}, "error": ""}
        rows.append(row)
        try:
            for kind_id in plan["kinds"]:
                _log(log, "%s: uploading %s" % (row["name"], KIND_LABEL[kind_id]), n, total)
                if kind_id == "pdf":
                    res = course_pdfs.push(ctx, cid, apply=True) or {}
                else:
                    res = gateway.push(ctx, cid, gateway.KINDS[kind_id], apply=True) or {}
                got = len(res.get("uploaded") or [])
                bad = len(res.get("failed") or []) + len(res.get("errors") or [])
                row["kinds"][kind_id] = {"uploaded": got, "failed": bad}
                uploaded += got
                failed += bad
        except Exception as exc:  # noqa: BLE001
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
            failed += 1
            _log(log, "%s: %s" % (cid, row["error"]), n, total)
    summary = {"ran_at": now_iso(), "action": "push", "applied": True,
               "kinds": plan["kinds"], "course_ids": plan["course_ids"],
               "rows": rows, "uploaded": uploaded, "failed": failed,
               "sentence": push_sentence(plan)}
    return _save(ctx, summary)
