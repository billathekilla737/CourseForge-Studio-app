"""Small helpers shared by the push and place modules."""
from __future__ import annotations

import html as htmlmod
import re
from typing import Callable


def visible_text(html: str) -> str:
    """The words a reader sees, whitespace collapsed, entities resolved."""
    h = re.sub(r"<(script|style)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = htmlmod.unescape(h).replace("\xa0", " ")
    return re.sub(r"\s+", " ", h).strip()


def same_text(a: str, b: str) -> bool:
    return visible_text(a) == visible_text(b)


def quiet_log(log: Callable | None) -> Callable:
    """A job's log sink, or a no-op when running from a test or the CLI."""
    if log is None:
        def _noop(message, done=None, total=None):
            return None
        return _noop
    return log


def num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def publish_word(publish: bool) -> str:
    return "published" if publish else "unpublished"


def course_url(base_url: str, course_id, tail: str = "") -> str:
    return f"{str(base_url).rstrip('/')}/courses/{course_id}{tail}"
