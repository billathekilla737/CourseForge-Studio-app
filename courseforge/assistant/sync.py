"""Pack this course's Assistant chat into Canvas user files, and hydrate it.

The replica is `courseforge-studio/state/assistant-<cid>.json`. It carries the
transcript (`conversation.txt`), the redacted event log, mode and model. It
does not carry `settings.json` (the hook path is this machine's), the system
prompt (rewritten on launch), or `names.json` (rebuilt from the live roster).

A Claude `session_id` only resumes on the machine that started it. Another PC
still shows the transcript; the next Send starts a new Claude session.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import statesync

TEXT_FILES = ("conversation.txt", "events.jsonl")
JSON_FILES = ("session.json", "mode.json", "model.json")
MAX_TEXT = 1_500_000

_APPLYING = threading.local()


def key(course_id) -> str:
    return statesync.assistant_key(course_id)


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > MAX_TEXT:
                fh.seek(max(0, size - MAX_TEXT))
                fh.readline()
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, (dict, list)) else None
    except (OSError, ValueError):
        return None


def pack(assistant_dir: Path, course_id, machine_id: str = "") -> dict:
    """JSON payload for one course's Assistant files."""
    root = Path(assistant_dir)
    files: dict = {}
    for name in TEXT_FILES:
        text = _read_text(root / name)
        if text:
            files[name] = text
    for name in JSON_FILES:
        data = _read_json(root / name)
        if data is not None:
            files[name] = data
    return {
        "course_id": str(course_id),
        "machine_id": machine_id or "",
        "updated": statesync._now(),
        "files": files,
    }


def apply(assistant_dir: Path, payload, machine_id: str = "") -> None:
    """Write a replica onto this computer. Drops a foreign Claude session_id."""
    root = Path(assistant_dir)
    root.mkdir(parents=True, exist_ok=True)
    files = (payload or {}).get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return
    _APPLYING.busy = True
    try:
        for name in TEXT_FILES:
            text = files.get(name)
            if isinstance(text, str):
                (root / name).write_text(text, encoding="utf-8")
        for name in JSON_FILES:
            if name == "session.json":
                continue
            data = files.get(name)
            if data is None:
                continue
            (root / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
        src_machine = str((payload or {}).get("machine_id") or "")
        session = files.get("session.json")
        session_path = root / "session.json"
        if isinstance(session, dict) and session.get("session_id") and src_machine and \
                src_machine == (machine_id or ""):
            session_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
        else:
            try:
                session_path.unlink()
            except OSError:
                pass
    finally:
        _APPLYING.busy = False


def _syncer(app):
    return getattr(app, "state_sync", None)


def _machine_id(app) -> str:
    return str((getattr(app, "machine", None) or {}).get("id") or "")


def _dir(app, course_id) -> Path:
    return Path(app.course_dir(course_id)) / "assistant"


def _session_alive(app, course_id) -> bool:
    mgr = getattr(app, "assistant", None)
    if mgr is None:
        return False
    try:
        c = mgr.course(course_id)
    except Exception:  # noqa: BLE001
        return False
    return bool(c.session and c.session.alive())


def hydrate(app, course_id) -> dict:
    """Pick up a replica unless a Claude session is live on this computer."""
    sync = _syncer(app)
    if sync is None:
        return {"did": "skipped", "reason": "no state syncer"}
    payload = pack(_dir(app, course_id), course_id, _machine_id(app))
    skip = _session_alive(app, course_id)
    mid = _machine_id(app)

    def _apply(body):
        apply(_dir(app, course_id), body, mid)

    return sync.hydrate(key(course_id), payload, apply=_apply, skip_pull=skip)


def push(app, course_id, force: bool = False) -> dict:
    """Upload the files on this computer. Safe to call from a turn's end."""
    if getattr(_APPLYING, "busy", False):
        return {"did": "skipped", "reason": "applying a replica"}
    sync = _syncer(app)
    if sync is None or not getattr(sync, "enabled", False):
        return {"did": "skipped"}
    payload = pack(_dir(app, course_id), course_id, _machine_id(app))
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
    payload = pack(_dir(app, course_id), course_id, _machine_id(app))
    mid = _machine_id(app)

    def _apply(body):
        apply(_dir(app, course_id), body, mid)

    return sync.resolve(key(course_id), take, local_payload=payload, apply=_apply)
