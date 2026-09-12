"""Small shared pieces for the course tools: where the area keeps its files,
JSON on disk, and the two exceptions the routes and the CLI both understand.

Every writing function in this package takes a `gate` callable instead of
calling the confirm gate itself. The routes hand in one that wraps
`app._gate(...)` with the page's token; the CLI hands in one that asks for a
typed yes. The module does its reads, builds the plan, calls `gate(...)` once
right before the first Canvas write, and never has to know which caller it is.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# gate(kind, payload, sentence, detail_rows) -> None, or raises.
Gate = Callable[[str, Any, str, list | None], None]
# log(message, done=None, total=None); the job sink is one, print-based ones too.
Log = Callable[..., None]

AREA = "courseops"


class Refused(Exception):
    """A plain-sentence refusal the person should read (a populated
    destination, an ambiguous program, a quiz with no questions)."""


def area_dir(app, course_id) -> Path:
    path = Path(app.course_dir(course_id)) / AREA
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def append_history(path: Path, entry: dict, keep: int = 100) -> list:
    rows = load_json(path, []) or []
    rows.append(entry)
    rows = rows[-keep:]
    save_json(path, rows)
    return rows


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def quiet_log(*_args, **_kwargs) -> None:
    """A log that says nothing, for callers that did not pass one."""


def as_int(value, default: int = 0) -> int:
    """course_counts() returns an int per kind, or an 'error: ...' string."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def date_part(iso: str | None) -> str | None:
    """'2026-08-24T05:00:00Z' -> '2026-08-24'; None stays None."""
    if not iso:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(iso))
    return m.group(1) if m else None


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    import html as _html
    t = re.sub(r"(?is)<script.*?</script>", " ", str(text))
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def course_label(course: dict | None, course_id) -> str:
    course = course or {}
    return course.get("name") or course.get("course_code") or f"course {course_id}"


def plural(n: int, word: str, words: str | None = None) -> str:
    return f"{n} {word if n == 1 else (words or word + 's')}"
