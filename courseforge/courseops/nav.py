"""Trim the course navigation to a keep-list, in order.

Ported from Trim-CanvasNav.ps1. The tabs endpoint answers 200 to a form body
and changes nothing (gotcha: JSON only), so every write goes through
`update_tab`, which sends JSON, and the nav is read back afterwards.

The keep-list is `nav_keep` in config.json when present, otherwise the
school's default below. LTI tab ids that do not exist in this course are
dropped from the plan rather than written (Canvas would 404). Home cannot be
hidden and Settings is teacher-only, so both are left alone.
"""
from __future__ import annotations

from pathlib import Path

from .. import ledger
from .common import AREA, Gate, Log, area_dir, course_label, load_json, now_iso, plural, quiet_log, save_json

DEFAULT_KEEP = [
    "home", "announcements", "syllabus", "modules",
    "context_external_tool_382357", "discussions", "grades", "people", "files",
    "context_external_tool_221916", "context_external_tool_342011",
]
LEAVE_ALONE = ("settings",)
PLAN_FILE = "nav/plan.json"
APPLY_FILE = "nav/last-apply.json"


def keep_list(cfg) -> list[str]:
    """`nav_keep` from config.json (the dataclass drops unknown keys, so read
    the raw file too), else the default."""
    keep = getattr(cfg, "nav_keep", None)
    if not keep:
        path = getattr(cfg, "_path", None)
        if path and Path(path).is_file():
            raw = load_json(Path(path), {}) or {}
            keep = raw.get("nav_keep")
    if isinstance(keep, dict):                      # {id: position} like the script
        keep = [k for k, _ in sorted(keep.items(), key=lambda kv: int(kv[1]))]
    if isinstance(keep, str):
        keep = [k.strip() for k in keep.replace("\n", ",").split(",") if k.strip()]
    return [str(k) for k in keep] if keep else list(DEFAULT_KEEP)


def check_base_url(cfg, base_url: str | None) -> None:
    """The token is only ever sent to the connected Canvas site."""
    if not base_url:
        return
    want = (cfg.base_url or "").rstrip("/").lower()
    got = str(base_url).rstrip("/").lower()
    if got != want:
        raise ValueError(f"{base_url} is not the connected course's Canvas site ({cfg.base_url}). "
                         "The token is not sent anywhere else.")


def plan(tabs: list[dict], keep: list[str] | None = None) -> dict:
    """Which tabs to hide, show or move, against the keep-list."""
    keep = [str(k) for k in (keep or DEFAULT_KEEP)]
    if "home" not in keep:
        keep = ["home"] + keep
    ids = {str(t.get("id")) for t in tabs}
    dropped = [k for k in keep if k not in ids]
    effective = [k for k in keep if k in ids]
    position = {tid: i + 1 for i, tid in enumerate(effective)}

    rows = []
    for t in sorted(tabs, key=lambda x: (x.get("position") or 999, str(x.get("id")))):
        tid = str(t.get("id"))
        hidden_now = bool(t.get("hidden"))
        row = {"id": tid, "label": t.get("label") or tid, "type": t.get("type") or "",
               "hidden_now": hidden_now, "position_now": t.get("position"),
               "lti": tid.startswith("context_external_tool_")}
        if tid in LEAVE_ALONE:
            row.update(action="leave", hidden_after=hidden_now, position_after=t.get("position"),
                       note="teacher-only; never changed")
        elif tid == "home":
            row.update(action="keep", hidden_after=False, position_after=1, note="cannot be hidden")
        elif tid in position:
            pos = position[tid]
            if hidden_now:
                action = "show"
            elif (t.get("position") or 0) != pos:
                action = "order"
            else:
                action = "keep"
            row.update(action=action, hidden_after=False, position_after=pos, note="")
        else:
            row.update(action="keep hidden" if hidden_now else "hide", hidden_after=True,
                       position_after=None, note="")
        rows.append(row)

    visible_after = [r["label"] for r in sorted(
        (r for r in rows if not r["hidden_after"] and r["id"] not in LEAVE_ALONE),
        key=lambda r: (r["position_after"] or 999))]
    now_sorted = sorted((t for t in tabs if not t.get("hidden") and str(t.get("id")) not in LEAVE_ALONE),
                        key=lambda t: (t.get("position") or 999))
    visible_now = [t.get("label") or str(t.get("id")) for t in now_sorted]
    visible_now_ids = [str(t.get("id")) for t in now_sorted]
    changes = [r for r in rows if r["action"] in ("hide", "show", "order")]
    return {"keep": effective, "dropped": dropped, "rows": rows, "changes": len(changes),
            "visible_now": visible_now, "visible_now_ids": visible_now_ids,
            "visible_after": visible_after}


def read_plan(app, course_id, keep: list[str] | None = None) -> dict:
    tabs = app.content.tabs(course_id)
    out = plan(tabs, keep or keep_list(app.cfg))
    out["course_id"] = str(course_id)
    out["default_keep"] = keep_list(app.cfg)
    out["computed_at"] = now_iso()
    save_json(area_dir(app, course_id) / PLAN_FILE, out)
    return out


def sentence_for(p: dict, label: str) -> str:
    hide = sum(1 for r in p["rows"] if r["action"] == "hide")
    show = sum(1 for r in p["rows"] if r["action"] == "show")
    order = sum(1 for r in p["rows"] if r["action"] == "order")
    parts = []
    if hide:
        parts.append(f"hide {plural(hide, 'tab')}")
    if show:
        parts.append(f"show {plural(show, 'tab')}")
    if order:
        parts.append(f"reorder {plural(order, 'tab')}")
    return (f"In the navigation of {label}: {', '.join(parts)}, leaving "
            f"{' > '.join(p['visible_after'])}. No content is changed; hidden tabs can be shown again "
            "from Settings.")


def apply(app, course_id, keep: list[str] | None, gate: Gate, log: Log = quiet_log) -> dict:
    """Write the plan with JSON bodies, then read the nav back and compare."""
    c = app.content
    course = c.course_detail(course_id) or {}
    label = course_label(course, course_id)
    p = read_plan(app, course_id, keep)
    changes = [r for r in p["rows"] if r["action"] in ("hide", "show", "order")]
    if not changes:
        return {**p, "written": 0, "note": "The navigation already matches the keep-list; nothing to write."}
    detail = [{"label": r["label"], "from": ("hidden" if r["hidden_now"] else f"shown, position {r['position_now']}"),
               "to": ("hidden" if r["hidden_after"] else f"shown, position {r['position_after']}")}
              for r in changes]
    gate("courseops.nav", {"course_id": str(course_id),
                           "changes": [{"id": r["id"], "hidden": r["hidden_after"], "position": r["position_after"]}
                                       for r in changes]},
         sentence_for(p, label), detail)

    results = []
    for i, r in enumerate(changes, 1):
        log(f"{r['action']} {r['label']}", i, len(changes))
        try:
            if r["action"] == "hide":
                c.update_tab(course_id, r["id"], hidden=True)
            else:
                c.update_tab(course_id, r["id"], hidden=False, position=r["position_after"])
            results.append({**r, "ok": True, "note": ""})
        except Exception as exc:  # noqa: BLE001
            results.append({**r, "ok": False, "note": f"{type(exc).__name__}: {exc}"})

    after = c.tabs(course_id)
    by_id = {str(t.get("id")): t for t in after}
    mismatches = 0
    for r in results:
        t = by_id.get(r["id"]) or {}
        got_hidden = bool(t.get("hidden"))
        r["got_hidden"] = got_hidden
        r["got_position"] = t.get("position")
        if r["ok"] and got_hidden != r["hidden_after"]:
            r["ok"] = False
            r["note"] = "Canvas answered 200 but the tab did not change (read back differs)"
            mismatches += 1
    visible = [t.get("label") or str(t.get("id")) for t in sorted(
        (t for t in after if not t.get("hidden") and str(t.get("id")) not in LEAVE_ALONE),
        key=lambda t: (t.get("position") or 999))]
    ok = sum(1 for r in results if r["ok"])
    ledger.record(app.course_dir(course_id), AREA,
                  f"Trimmed the navigation of {label} to {' > '.join(visible)} ({plural(ok, 'tab')} changed)",
                  url=f"{app.cfg.base_url}/courses/{course_id}/settings", count=ok, kind="nav",
                  undo={"route": f"/tools/{course_id}/nav", "body": {"apply": True, "keep": p["visible_now_ids"]}}
                  if p.get("visible_now_ids") else None)
    out = {**p, "written": ok, "failed": len(results) - ok, "mismatches": mismatches,
           "results": results, "visible_after_live": visible, "at": now_iso()}
    save_json(area_dir(app, course_id) / APPLY_FILE, out)
    log(f"visible nav: {'  >  '.join(visible)}")
    return out


def plan_text(p: dict) -> str:
    lines = [f"{'Tab':<28} {'Now':<20} {'After':<20} Action"]
    for r in p["rows"]:
        now = "hidden" if r["hidden_now"] else f"shown #{r['position_now']}"
        aft = "hidden" if r["hidden_after"] else f"shown #{r['position_after']}"
        lines.append(f"{r['label'][:28]:<28} {now:<20} {aft:<20} {r['action']}"
                     + (f"  ({r['note']})" if r.get("note") else ""))
    if p.get("dropped"):
        lines.append(f"not in this course (skipped): {', '.join(p['dropped'])}")
    lines.append(f"visible after: {'  >  '.join(p['visible_after'])}")
    return "\n".join(lines)


def last_apply(app, course_id) -> dict | None:
    return load_json(area_dir(app, course_id) / APPLY_FILE)


__all__ = ["DEFAULT_KEEP", "keep_list", "check_base_url", "plan", "read_plan", "apply",
           "sentence_for", "plan_text", "last_apply"]
