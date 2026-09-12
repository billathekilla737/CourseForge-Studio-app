"""Carry grading in progress between machines, through Canvas itself.

Grade at home, stop halfway through the comments, finish at work. The problem
is not the grades -- those can go to the gradebook -- but everything around
them: the rationales, the review flags, the curve, which students you have
already looked at, and the minutes of Blender rendering behind the contact
sheets.

Canvas carries it. Not the course's files, which a course copy would drag into
next semester's shell along with last term's grades, but your own user files,
which belong to the account and are untouched by copy, export and sandbox
cleanups. Nothing lands anywhere the student work is not already.

Two artifacts per assignment, because they change at completely different rates:

    canvas-grader/<course>-<assignment>.draft.json    ~40 KB, rewritten often
    canvas-grader/<course>-<assignment>.blend.zip     ~17 MB, once per pass

The draft moves on every edit. The Blender bundle is effectively immutable once
rendered, so it is uploaded when a pass finishes and then costs nothing.

What deliberately does NOT travel:

  * the raw .blend files and every other attachment, because Canvas hands those
    back for free on the next sync, and shipping them is paying twice;
  * the five view_*.png per student, because contact.png is their composite and
    the only one anything reads;
  * map.json, which turns pseudonyms back into real names. It is regenerated
    locally from the roster, the draft is keyed by user id, and the most
    sensitive file in the tree has no reason to leave the machine.
"""
from __future__ import annotations

import hashlib
import io
import json
import socket
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
FOLDER = "canvas-grader"
# One student's share of a bundle: the report, the contact sheet, the preview.
BLEND_PARTS = ("stats.json", "contact.png", "model.glb")


# --------------------------------------------------------------- this machine
def machine(user_dir: Path) -> dict:
    """A stable name for this computer, so a handoff can say where it came from.

    Kept outside the project folder on purpose. Inside it, copying the directory
    to a second machine would copy the identity with it, and both would claim to
    be the same one.
    """
    path = Path(user_dir) / "machine.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("id"):
            return saved
    except (OSError, json.JSONDecodeError):
        pass
    import secrets
    info = {"id": secrets.token_hex(6), "name": socket.gethostname() or "unknown"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(info, indent=2), encoding="utf-8")
        tmp.replace(path)
        # Two processes starting for the first time at once would otherwise each
        # keep the id it invented, while only one of them is on disk. Read back
        # whichever won so they converge on the same answer.
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("id"):
            return saved
    except (OSError, json.JSONDecodeError):
        pass
    return info


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- naming
def draft_name(course_id, assignment_id) -> str:
    return f"{course_id}-{assignment_id}.draft.json"


def blend_name(course_id, assignment_id) -> str:
    return f"{course_id}-{assignment_id}.blend.zip"


# ------------------------------------------------------- local bookkeeping
# A sidecar beside the draft, never uploaded. Its presence is what marks an
# assignment as taking part: nothing is sent anywhere until you hand off once.

def state_path(adir: Path) -> Path:
    return Path(adir) / "handoff.json"


def local_state(adir: Path) -> dict:
    try:
        return json.loads(state_path(adir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(adir: Path, **changes) -> dict:
    state = local_state(adir)
    state.update(changes)
    state["at"] = _now()
    tmp = state_path(adir).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path(adir))
    return state


def enabled(adir: Path) -> bool:
    return state_path(adir).is_file()


def fingerprint(payload) -> str:
    """A stable hash of anything, for "has this changed since I sent it?"."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# What counts as the grading having changed. Deliberately not the whole draft: a
# pull rewrites pulled_at and synced_at on every timer tick, so hashing those
# would leave the worker uploading every few seconds for ever while nobody was
# grading at all.
GRADED_FIELDS = ("scores", "total", "comment", "rationales", "flags", "source",
                 "needs_human", "needs_human_reason", "human_ok", "curve",
                 "confidence", "total_only", "model")


def grading_fingerprint(draft: dict, instructions: str = "") -> str:
    """A hash of the work itself: scores, comments, rationales, review marks."""
    students = {
        uid: {k: entry.get(k) for k in GRADED_FIELDS if k in entry}
        for uid, entry in sorted((draft.get("students") or {}).items())
    }
    return fingerprint({
        "students": students,
        "rubric": draft.get("rubric"),
        "points_possible": draft.get("points_possible"),
        "instructions": instructions or "",
    })


# ----------------------------------------------------------------- the draft
def build_envelope(draft: dict, instructions: str, rev: int, who: dict,
                   course_id, assignment_id) -> bytes:
    graded = sum(1 for e in (draft.get("students") or {}).values()
                 if e.get("total") is not None)
    envelope = {
        "schema": SCHEMA,
        "rev": int(rev),
        "machine": who,
        "written_at": _now(),
        "course_id": str(course_id),
        "assignment_id": str(assignment_id),
        # Carried so the pickup dialog can describe what is waiting without
        # downloading and parsing the whole thing first.
        "assignment_name": draft.get("assignment_name") or "",
        "students": len(draft.get("students") or {}),
        "graded": graded,
        "draft": draft,
        "instructions": instructions or "",
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def read_envelope(raw: bytes) -> dict:
    env = json.loads(raw.decode("utf-8"))
    if not isinstance(env, dict) or "draft" not in env:
        raise ValueError("that file is not a grading handoff")
    if int(env.get("schema") or 0) > SCHEMA:
        raise ValueError(
            "that handoff was written by a newer version of this tool. Update "
            "here before picking it up, or it would be read wrong.")
    return env


# --------------------------------------------------------- the Blender bundle
def blend_sources(adir: Path) -> list[Path]:
    """Every artifact worth carrying, and nothing that can be rebuilt."""
    blend = Path(adir) / "blend"
    if not blend.is_dir():
        return []
    found = []
    for name in BLEND_PARTS:
        found += sorted(blend.glob(f"*/*/{name}"))
    return found


def build_blend_zip(adir: Path) -> bytes | None:
    files = blend_sources(adir)
    if not files:
        return None
    adir = Path(adir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            zf.write(path, str(path.relative_to(adir)).replace("\\", "/"))
    return buf.getvalue()


def blend_fingerprint(adir: Path) -> str:
    """Names and sizes of what a bundle would hold. Cheap enough to check often,
    and it moves whenever a pass actually re-rendered something."""
    adir = Path(adir)
    parts = []
    for path in blend_sources(adir):
        try:
            parts.append(f"{path.relative_to(adir)}:{path.stat().st_size}")
        except OSError:
            continue
    return fingerprint(sorted(parts))


def apply_blend_zip(adir: Path, raw: bytes) -> int:
    """Unpack a bundle into the assignment folder. Returns files written.

    Entries are checked against the assignment directory before anything is
    written: a zip is an untrusted archive, and "../" in a member name would
    otherwise land wherever it liked.
    """
    adir = Path(adir).resolve()
    written = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            dest = (adir / member.filename).resolve()
            if not str(dest).startswith(str(adir)):
                continue                      # refuses to escape the folder
            if dest.name not in BLEND_PARTS:
                continue                      # and refuses anything unexpected
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src:
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                tmp.write_bytes(src.read())
                tmp.replace(dest)
            written += 1
    return written
