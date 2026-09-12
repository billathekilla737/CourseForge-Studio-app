"""Checking a quiz settings edit before it reaches Canvas.

The editor in the browser sends a small dict of proposed settings. This module
is what decides whether each one is a real change and a legal value, turns it
into the shape Canvas wants, and writes the sentence the confirmation screen
shows. Nothing is trusted on the way in: the field must be one this tool edits,
the value must parse, and it must actually differ from what the quiz already
holds -- an edit that changes nothing is dropped rather than sent.

Dates arrive as UTC from the browser, which has already turned the instructor's
wall clock into an instant. Nothing here guesses a timezone.
"""
from __future__ import annotations

import re
from datetime import datetime

# field -> (kind, human label). The kind drives both the check and the wording.
FIELDS: dict[str, tuple[str, str]] = {
    "due_at": ("date", "Due"),
    "unlock_at": ("date", "Available from"),
    "lock_at": ("date", "Available until"),
    "time_limit": ("minutes", "Time limit"),
    "access_code": ("text", "Password"),
    "ip_filter": ("text", "IP filter"),
    "allowed_attempts": ("attempts", "Attempts allowed"),
    "scoring_policy": ("choice:keep_highest,keep_latest", "If retaken, keep"),
    "shuffle_answers": ("bool", "Shuffle answers"),
    "one_question_at_a_time": ("bool", "One question at a time"),
    "cant_go_back": ("bool", "Lock each question once answered"),
    "one_time_results": ("bool", "Let students see results once only"),
    "show_correct_answers": ("bool", "Show correct answers"),
    "hide_results": ("choice:,always,until_after_last_attempt",
                     "Hide results from students"),
    "published": ("bool", "Published"),
}

# The date pattern the browser sends: an ISO instant in UTC.
RE_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?Z$")

MAX_TIME_LIMIT_MIN = 1440       # a day. Anything more is a typo, not a setting.


class Rejected(ValueError):
    """A proposed setting that will not be sent, with the reason."""


def validate(proposed: dict, quiz: dict) -> dict:
    """Compare a proposed edit against the quiz as Canvas currently has it.

    Returns {"changes": {...}, "described": [...], "rejected": [...]} where
    `changes` is exactly what to hand to CanvasClient.update_quiz_settings.
    """
    changes: dict = {}
    described: list[dict] = []
    rejected: list[dict] = []

    for name, raw in (proposed or {}).items():
        if name not in FIELDS:
            rejected.append({"field": name,
                             "why": f"{name} is not a setting this tool edits"})
            continue
        kind, label = FIELDS[name]
        try:
            value = _coerce(kind, raw, label)
        except Rejected as exc:
            rejected.append({"field": name, "why": str(exc)})
            continue

        current = _current(kind, quiz.get(name))
        if _same(kind, current, value):
            continue                    # not a change; say nothing, send nothing

        changes[name] = value
        # A password or IP filter is never echoed back, so "set -> set" is all
        # a straight render could say. Name the transition instead.
        if kind == "text" and current and value:
            shown_to = "replaced"
        else:
            shown_to = _show(kind, value)
        described.append({
            "field": name, "label": label,
            "from": _show(kind, current), "to": shown_to,
        })

    _cross_check(changes, quiz, rejected)
    for gone in [d["field"] for d in described if d["field"] not in changes]:
        described = [d for d in described if d["field"] != gone]

    return {"changes": changes, "described": described, "rejected": rejected}


def _coerce(kind: str, raw, label: str):
    if kind == "date":
        if raw in (None, "", "null"):
            return None
        text = str(raw).strip()
        if not RE_UTC.match(text):
            raise Rejected(f"{label} did not arrive as a UTC instant "
                           f"({text[:30]!r})")
        return text
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
        raise Rejected(f"{label} must be on or off, not {text[:20]!r}")
    if kind == "minutes":
        if raw in (None, "", "null", 0, "0"):
            return None                 # no limit
        try:
            minutes = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise Rejected(f"{label} must be a number of minutes") from None
        if not 1 <= minutes <= MAX_TIME_LIMIT_MIN:
            raise Rejected(f"a {minutes}-minute limit is outside 1 minute to "
                           f"{MAX_TIME_LIMIT_MIN} (a day)")
        return minutes
    if kind == "attempts":
        if raw in (None, "", "null"):
            raise Rejected("attempts allowed cannot be blank")
        try:
            n = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise Rejected("attempts allowed must be a number, or -1 for "
                           "unlimited") from None
        if n == 0 or n < -1 or n > 100:
            raise Rejected(f"{n} attempts is not a value Canvas takes "
                           "(1 to 100, or -1 for unlimited)")
        return n
    if kind == "text":
        text = "" if raw is None else str(raw).strip()
        if len(text) > 200:
            raise Rejected(f"{label} is too long ({len(text)} characters)")
        return text or None
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        text = "" if raw is None else str(raw).strip()
        if text not in allowed:
            shown = ", ".join(a or "(off)" for a in allowed)
            raise Rejected(f"{label} must be one of {shown}")
        return text or None
    raise Rejected(f"cannot check a {kind} setting")


def _current(kind: str, raw):
    """Canvas's present value, normalised the same way as a proposed one."""
    if kind == "bool":
        return bool(raw)
    if kind in ("minutes",):
        return int(raw) if raw else None
    if kind == "attempts":
        return int(raw) if raw not in (None, "") else None
    if kind == "text" or kind.startswith("choice:"):
        text = "" if raw is None else str(raw).strip()
        return text or None
    if kind == "date":
        return str(raw) if raw else None
    return raw


def _same(kind: str, a, b) -> bool:
    if kind == "date":
        return _instant(a) == _instant(b)
    return a == b


def _instant(text):
    """Compare dates as instants, so 04:59:00Z and 04:59Z are one date."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:19].replace("Z", ""),
                                 "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return str(text)


def _cross_check(changes: dict, quiz: dict, rejected: list) -> None:
    """Rules that only make sense across two fields at once."""
    def after(a: str, b: str) -> bool:
        first = _instant(changes.get(a, quiz.get(a)))
        second = _instant(changes.get(b, quiz.get(b)))
        return bool(first and second and isinstance(first, datetime)
                    and isinstance(second, datetime) and first > second)

    if after("unlock_at", "lock_at"):
        for name in ("unlock_at", "lock_at"):
            changes.pop(name, None)
        rejected.append({"field": "unlock_at",
                         "why": "that would open the quiz after it closes"})

    if after("due_at", "lock_at"):
        rejected.append({"field": "due_at",
                         "why": "the due date is after the quiz locks, so "
                                "nobody could submit on time -- sent anyway, "
                                "but check it"})

    # Locking each question once answered only means anything one at a time.
    one_at_a_time = changes.get("one_question_at_a_time",
                                bool(quiz.get("one_question_at_a_time")))
    if changes.get("cant_go_back") and not one_at_a_time:
        changes.pop("cant_go_back", None)
        rejected.append({"field": "cant_go_back",
                         "why": "Canvas only offers this with one question at "
                                "a time, so turn that on first"})


def summary(described: list[dict], quiz: dict) -> str:
    """The sentence the confirmation screen leads with."""
    if not described:
        return "Nothing to change."
    title = str(quiz.get("title") or "this quiz")
    if len(described) == 1:
        d = described[0]
        return f"{title}: {d['label']} {d['from']} -> {d['to']}"
    return (f"{title}: {len(described)} settings -- "
            + ", ".join(d["label"] for d in described))


def _show(kind: str, value) -> str:
    if value is None or value == "":
        return {"date": "none", "minutes": "no limit",
                "text": "none"}.get(kind, "off")
    if kind == "bool":
        return "on" if value else "off"
    if kind == "minutes":
        return f"{value} min"
    if kind == "attempts":
        return "unlimited" if value == -1 else str(value)
    if kind == "text":
        return "set"                    # never echo a password back
    if kind == "date":
        return str(value)               # the browser renders this locally
    return str(value)
