"""Pack unpublished Build drafts into Canvas user files, and hydrate them.

The replica is `courseforge-studio/state/build-<cid>.json`. It carries
`drafts/*.json`, `state.json`, `manifest.json` and `rubrics.json`. It does not
carry `backups/` (the previous syllabus body) or `last_push.json` (a local job
result). Nothing here is student data.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import statesync
from .paths import BuildDir

JSON_FILES = ("state.json", "manifest.json", "rubrics.json")
DRAFT_BUDGET = 2_000_000
_APPLYING = threading.local()


def key(course_id) -> str:
    return statesync.build_key(course_id)


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, (dict, list)) else None
    except (OSError, ValueError):
        return None


def _trim_drafts(drafts: list[dict], budget: int = DRAFT_BUDGET) -> dict:
    """Keep unpublished drafts first; drop oldest placed copies if the blob is big."""
    unpublished = [d for d in drafts if d.get("id") and not d.get("placed")]
    placed = [d for d in drafts if d.get("id") and d.get("placed")]
    keep = unpublished + placed
    out = {str(d["id"]): d for d in keep}
    while keep:
        blob = json.dumps(out, sort_keys=True, separators=(",", ":"), default=str)
        if len(blob.encode("utf-8")) <= budget:
            break
        if len(keep) <= len(unpublished) and len(unpublished) <= 1:
            break
        keep.pop()
        out = {str(d["id"]): d for d in keep}
    return out


def pack(build: BuildDir, machine_id: str = "") -> dict:
    files: dict = {}
    for name in JSON_FILES:
        data = _read_json(build.root / name)
        if data is not None:
            files[name] = data
    files["drafts"] = _trim_drafts(build.drafts())
    return {
        "course_id": str(build.course_id),
        "machine_id": machine_id or "",
        "updated": statesync._now(),
        "files": files,
    }


def apply(build: BuildDir, payload) -> None:
    files = (payload or {}).get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return
    _APPLYING.busy = True
    try:
        for name in JSON_FILES:
            data = files.get(name)
            if data is None:
                continue
            build.write_json(build.root / name, data)
        drafts = files.get("drafts") or {}
        if not isinstance(drafts, dict):
            drafts = {}
        seen = set()
        build.drafts_dir.mkdir(parents=True, exist_ok=True)
        for rec in drafts.values():
            if isinstance(rec, dict) and rec.get("id"):
                build.write_json(build.draft_path(rec["id"]), rec)
                seen.add(str(rec["id"]))
        if build.drafts_dir.is_dir():
            for path in list(build.drafts_dir.glob("*.json")):
                rec = build.read_json(path)
                did = str((rec or {}).get("id") or path.stem)
                if did not in seen:
                    try:
                        path.unlink()
                    except OSError:
                        pass
    finally:
        _APPLYING.busy = False


def _syncer(app):
    return getattr(app, "state_sync", None)


def _machine_id(app) -> str:
    return str((getattr(app, "machine", None) or {}).get("id") or "")


def _build(app, course_id) -> BuildDir:
    return BuildDir(app.course_dir(course_id), course_id)


def hydrate(app, course_id) -> dict:
    sync = _syncer(app)
    if sync is None:
        return {"did": "skipped", "reason": "no state syncer"}
    build = _build(app, course_id)
    payload = pack(build, _machine_id(app))

    def _apply(body):
        apply(build, body)

    return sync.hydrate(key(course_id), payload, apply=_apply)


def push(app, course_id, force: bool = False) -> dict:
    if getattr(_APPLYING, "busy", False):
        return {"did": "skipped", "reason": "applying a replica"}
    sync = _syncer(app)
    if sync is None or not getattr(sync, "enabled", False):
        return {"did": "skipped"}
    payload = pack(_build(app, course_id), _machine_id(app))
    try:
        return sync.put(key(course_id), payload, force=force)
    except statesync.Conflict as exc:
        return {"did": "diverged", "reason": str(exc),
                "remote_machine": ((exc.remote or {}).get("machine") or {}).get("name") or ""}
    except Exception as exc:  # noqa: BLE001
        sync.mark(key(course_id))
        return {"did": "error", "reason": f"{type(exc).__name__}: {exc}"}


def resolve(app, course_id, take: str) -> dict:
    sync = _syncer(app)
    if sync is None:
        raise RuntimeError("no state syncer")
    build = _build(app, course_id)
    payload = pack(build, _machine_id(app))

    def _apply(body):
        apply(build, body)

    return sync.resolve(key(course_id), take, local_payload=payload, apply=_apply)
