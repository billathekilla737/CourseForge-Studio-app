"""The per-course build state: what this tool created, keyed by manifest key.

Ported from the state files of Push-CanvasPages.ps1 (canvas.state.<id>.json)
and Push-CanvasProject.ps1 (canvas.project.<id>.json), merged into one file
per course at data/<cid>/build/state.json.

    {
      "course_id": "12345",
      "pages":       {key: {"url": slug, "page_id": n, "title": "..."}},
      "modules":     {name: module_id},
      "items":       {"<module_id>::<page_url>": item_id},
      "assignments": {key: id}, "discussions": {key: id}, "quizzes": {key: id},
      "module_order": [names in the last rebuild],
      "built_modules": true when a project push rebuilt the modules
    }

`owns(course_id)` is the module-wipe gate's evidence: only a state file that
names THIS course and records a module build counts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .paths import BuildDir


def empty(course_id) -> dict:
    return {"course_id": str(course_id), "pages": {}, "modules": {}, "items": {},
            "assignments": {}, "discussions": {}, "quizzes": {}, "module_order": [],
            "built_modules": False, "updated_at": None}


def load(build: BuildDir) -> dict:
    raw = build.read_json(build.state_path)
    state = empty(build.course_id)
    if not isinstance(raw, dict):
        return state
    if raw.get("course_id") and str(raw["course_id"]) != build.course_id:
        raise ValueError(f"The state file {build.state_path} belongs to course "
                         f"{raw['course_id']}, not {build.course_id}. It was not used.")
    for key in state:
        if key in raw and raw[key] is not None:
            state[key] = raw[key]
    # Pages written by the legacy script are {url, page_id, title} already; a
    # project state kept only the slug. Normalise to the dict form.
    for k, v in list(state["pages"].items()):
        if isinstance(v, str):
            state["pages"][k] = {"url": v, "page_id": None, "title": None}
    return state


def save(build: BuildDir, state: dict) -> Path:
    state["course_id"] = build.course_id
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return build.write_json(build.state_path, state)


def owns(state: dict | None, course_id) -> bool:
    """True when this tool built the modules of `course_id` (the wipe gate)."""
    if not isinstance(state, dict):
        return False
    if str(state.get("course_id") or "") != str(course_id):
        return False
    return bool(state.get("built_modules") or state.get("modules") or state.get("module_order"))
