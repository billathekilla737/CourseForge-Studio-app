"""The course hub: where each area stands in one course, from local state only.

Three routes. The hub payload is assembled from what is already on disk (the
cached assignment list, draft files, each area's own manifests through its
`hub_status`, the ledger) so it paints in tens of milliseconds and never calls
Canvas. The one Canvas read is the refresh job, which re-reads the assignment
list the way the grader's Refresh button does, then returns a fresh hub.
"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import ledger
from ..areas import AREAS
from ..routing import route

# Which hub card each area package reports into. Accessibility is three
# packages (HTML restyling, Office documents, PDFs) and one card.
CARD_OF = {
    "a11y": "a11y", "docs": "a11y", "pdf": "a11y",
    "content": "build", "courseops": "tools", "assistant": "assistant",
}
CARDS = ("grade", "a11y", "build", "tools", "assistant")

# An assignment list older than this is shown at once and refreshed behind it.
STALE_AFTER_S = 6 * 3600


def install(app) -> None:  # noqa: ARG001  (the routes register on import)
    return None


# --------------------------------------------------------------------- routes
@route("GET", "/api/courses/{cid}/hub", area="hub")
def hub(req):
    return build_hub(req.app, req.params["cid"])


@route("GET", "/api/courses/{cid}/ledger", area="hub")
def ledger_rows(req):
    cid = req.params["cid"]
    try:
        limit = max(1, min(int(req.q("limit", "50")), 500))
    except ValueError:
        limit = 50
    rows = ledger.read(req.app.course_dir(cid), limit=limit, area=req.q("area") or None)
    return {"course_id": cid, "rows": rows}


@route("POST", "/api/courses/{cid}/hub/refresh", area="hub")
def refresh(req):
    cid = req.params["cid"]
    app = req.app

    def job(log):
        log("Reading the assignment list from Canvas (reads only)", 0, 2)
        rows = app.assignments(cid, refresh=True)
        log(f"{len(rows)} assignments in the list", 1, 2)
        out = build_hub(app, cid)
        log("Hub updated", 2, 2)
        return out

    return req.job("hub.refresh", job)


# ------------------------------------------------------------------ the hub
def build_hub(app, cid) -> dict:
    cdir = Path(app.course_dir(cid))
    course = _course(app, cid)
    grade = _grade_status(app, cid, cdir)
    stale = bool(grade.pop("_stale", False))

    modules = {name: _module_status(app, name, cid) for name in AREAS}
    areas: dict[str, dict] = {"grade": grade}
    for card in CARDS[1:]:
        parts = [(name, modules[name]) for name in AREAS if CARD_OF.get(name) == card]
        areas[card] = _merge(card, parts, cid)
    # The raw per-package statuses too, so a sub-area's badge can be read by
    # its own name (the PDF tab asks for hub.pdf.badge).
    for name, status in modules.items():
        areas.setdefault(name, status)

    rows = ledger.read(cdir, limit=8)
    needs = sorted({n for a in areas.values() for n in (a.get("needs") or []) if n})
    writes_today = ledger.today_counts(cdir).get("writes", 0)
    stats = [
        {"label": "Assignments", "value": grade.get("count", 0)},
        {"label": "Waiting to grade", "value": grade.get("waiting", 0),
         "kind": "warn" if grade.get("waiting") else ""},
        {"label": "Graded here", "value": grade.get("graded", 0)},
        {"label": "Canvas writes today", "value": writes_today},
    ]
    return {
        "course": course,
        "stats": stats,
        "areas": areas,
        "needs": needs,
        "ledger": rows,
        "stale": stale,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _course(app, cid) -> dict:
    for c in app.store.courses() or []:
        if str(c.get("id")) == str(cid):
            return {
                "id": c.get("id"), "name": c.get("name") or f"Course {cid}",
                "code": c.get("code"), "term_label": c.get("term_label") or c.get("term"),
                "students": c.get("students"),
            }
    return {"id": cid, "name": f"Course {cid}"}


# ----------------------------------------------------------------- grading
def _grade_status(app, cid, cdir: Path) -> dict:
    """The Grade card, from the cached assignment list and the draft files.
    Counts and timestamps only: nothing about a student leaves this function."""
    store = app.store
    cache = cdir / "assignments.json"
    assignments = store.assignments(cid) or []
    waiting = sum(int(a.get("needs_grading") or 0) for a in assignments)

    graded = 0
    last: str | None = None
    try:
        children = [p for p in cdir.iterdir() if p.is_dir() and p.name.isdigit()]
    except OSError:
        children = []
    for child in children:
        draft = store.read(child / "draft.json", None)
        if not isinstance(draft, dict) or not draft:
            continue
        when = draft.get("last_graded_at") or draft.get("updated_at")
        if draft.get("last_graded_at") or draft.get("students"):
            graded += 1
        if when and (last is None or str(when) > str(last)):
            last = str(when)

    lines: list[str] = []
    if cache.is_file():
        lines.append(f"{len(assignments)} assignment{'s' if len(assignments) != 1 else ''} in the list"
                     + (f", {waiting} submission{'s' if waiting != 1 else ''} waiting to grade" if waiting else ""))
    else:
        lines.append("The assignment list has not been read from Canvas yet. "
                     "Open Grade to read it; nothing is written.")
    if graded:
        lines.append(f"{graded} graded here" + (f", last {_ago(last)}" if last else ""))
    else:
        lines.append("Nothing graded here yet. Grades are pushed hidden, and only when you confirm.")

    try:
        age = time.time() - cache.stat().st_mtime if cache.is_file() else None
    except OSError:
        age = None
    stale = age is None or age > STALE_AFTER_S

    recent = sorted(assignments, key=lambda a: str(a.get("graded_at") or a.get("synced_at") or ""), reverse=True)
    return {
        "installed": True,
        "lines": lines,
        "badge": waiting or None,
        "badge_title": "submissions Canvas says are waiting to grade",
        "needs": [],
        "actions": [{"label": "Open assignments", "href": f"#/c/{cid}/grade"}],
        "count": len(assignments),
        "waiting": waiting,
        "graded": graded,
        "last_graded_at": last,
        "assignments": [
            {"id": a.get("id"), "name": a.get("name", ""), "graded_at": a.get("graded_at"),
             "synced_at": a.get("synced_at"), "needs_grading": a.get("needs_grading")}
            for a in recent[:200]
        ],
        "_stale": stale,
    }


# ------------------------------------------------------------------- areas
def _not_installed(detail: str = "") -> dict:
    return {"installed": False, "lines": ["Not installed"], "badge": None, "needs": [],
            "actions": [], **({"detail": detail} if detail else {})}


def _module_status(app, name: str, cid) -> dict:
    status = (getattr(app, "area_status", None) or {}).get(name) or {}
    if not status.get("ok"):
        return _not_installed(status.get("error", ""))
    try:
        module = importlib.import_module(f"courseforge.{name}.routes")
    except Exception as exc:  # noqa: BLE001
        return _not_installed(f"{type(exc).__name__}: {exc}")
    fn = getattr(module, "hub_status", None)
    if fn is None:
        return {"installed": True, "lines": [], "badge": None, "needs": [], "actions": []}
    try:
        raw = fn(app, cid) or {}
    except Exception as exc:  # noqa: BLE001
        return {"installed": True, "lines": [], "badge": None, "needs": [], "actions": [],
                "error": f"Status unavailable: {type(exc).__name__}: {exc}"}
    return _normalise(raw)


def _normalise(raw: dict) -> dict:
    lines = raw.get("lines") or []
    if isinstance(lines, str):
        lines = [lines]
    badge = raw.get("badge")
    if badge in ("", 0, "0"):
        badge = None
    out = {
        "installed": True,
        "lines": [str(x) for x in lines if x],
        "badge": badge,
        "needs": [str(x) for x in (raw.get("needs") or []) if x],
        "actions": [a for a in (raw.get("actions") or []) if isinstance(a, dict) and a.get("label")],
    }
    for key in ("badge_title", "error", "summary"):
        if raw.get(key):
            out[key] = raw[key]
    # Anything else the area reported rides along for its own tab to read.
    for key, value in raw.items():
        if key not in out and key not in ("lines", "badge", "needs", "actions"):
            out[key] = value
    return out


def _merge(card: str, parts: list[tuple[str, dict]], cid) -> dict:
    installed = [(n, p) for n, p in parts if p.get("installed")]
    if not installed:
        return _not_installed()
    lines: list[str] = []
    needs: list[str] = []
    actions: list[dict] = []
    badge_total = 0
    badge_seen = False
    error = None
    for _name, p in installed:
        lines.extend(p.get("lines") or [])
        for n in p.get("needs") or []:
            if n not in needs:
                needs.append(n)
        actions.extend(p.get("actions") or [])
        b = p.get("badge")
        if isinstance(b, (int, float)) and b:
            badge_total += int(b)
            badge_seen = True
        elif b not in (None, "", 0):
            badge_seen = True
            badge_total += 1
        error = error or p.get("error")
    out = {
        "installed": True,
        "lines": lines,
        "badge": badge_total if badge_seen else None,
        "needs": needs,
        "actions": actions or [{"label": f"Open {card.capitalize() if card != 'a11y' else 'Accessibility'}",
                                "href": f"#/c/{cid}/{card}"}],
        "modules": {n: p for n, p in parts},
    }
    if error:
        out["error"] = error
    return out


# ------------------------------------------------------------------ helpers
def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if when.tzinfo is None:
        when = when.astimezone()
    seconds = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    if hours < 36:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"
