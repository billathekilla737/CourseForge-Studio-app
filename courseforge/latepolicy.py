"""Late-work rules from a course syllabus, applied after the auto-grader scores.

Claude grades the work as if it were on time. This module reads the syllabus
(and, when Canvas already has a late policy, that too), turns the prose into a
percent, and docks the draft total. The earned rubric scores stay as they are.

If Canvas itself is already deducting, Studio does not dock a second time.
"""
from __future__ import annotations

import re
from typing import Callable

# Titles and bodies that are how this course treats late work — not "later in
# the term" and not a guess.
LATE_HINT = re.compile(
    r"\blate\b|"
    r"late\s*[- ]?\s*(works?|assignments?|submissions?|papers?|turn[- ]?in|polic)|"
    r"work\s+turned\s+in\s+late|"
    r"submitted\s+(?:after\s+the\s+due\s+date|late)|"
    r"(?:after|past)\s+the\s+due\s+date|"
    r"penalty\s+for\s+late|"
    r"late\s+penalty|"
    r"grace\s+period|"
    r"not\s+accept(?:ed)?\s+late|"
    r"no\s+late\s+work",
    re.I)
SYLLABUS_TITLE = re.compile(r"\bsyllabus\b|course\s+polic|late\s+work|late\s+polic", re.I)

APPLY_KINDS = frozenset({
    "percent_per_day", "percent_per_hour", "flat_percent", "none_accepted",
})

_PER_INTERVAL = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:percent|%)"
    r"(?:\s+\w+){0,8}?\s+"
    r"(?:per|each|a|/)\s+(?:calendar\s+)?(day|hour)s?\b",
    re.I)
_PER_INTERVAL_FLIP = re.compile(
    r"(?:lose|loses|lost|deduct(?:ed|ion)?|taken|take)\s+"
    r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s*"
    r"(?:per|a|each|/)\s*(?:calendar\s+)?(day|hour)s?\b",
    re.I)
_LETTER_PER_DAY = re.compile(
    r"(?:one|1|a)\s+letter\s+grade\s+(?:per|a|each)\s+day",
    re.I)
_FLAT_PERCENT = re.compile(
    r"(?:late[^.]*?|if\s+(?:it\s+is\s+|they\s+are\s+)?late[^.]*?)"
    r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s*"
    r"(?:credit|penalty|reduction|deduction|off)",
    re.I)
_HALF_CREDIT = re.compile(
    r"half\s+credit|50\s*(?:percent|%)\s+credit",
    re.I)
_NONE = re.compile(
    r"no\s+late\s+work|"
    r"late\s+(?:work|assignments?|submissions?)\s+"
    r"(?:will\s+|is\s+|are\s+)?(?:not|never|won'?t)\s+"
    r"(?:be\s+)?(?:accepted|allowed|permitted|graded)|"
    r"will\s+not\s+accept\s+late|"
    r"not\s+accept(?:ed)?\s+late|"
    r"late\s+(?:work|assignments?)\s+is\s+not\s+accepted",
    re.I)
_ACCEPTED_NO_PENALTY = re.compile(
    r"(?:late\s+(?:work|assignments?)\s+)?(?:accepted|allowed)\s+"
    r"(?:without|with\s+no)\s+penalty|"
    r"no\s+penalty\s+for\s+late",
    re.I)
_GRACE = re.compile(
    r"(?:grace\s+period|grace)\s*(?:of\s+)?(\d+(?:\.\d+)?)\s*(hour|hr|day)s?\b|"
    r"(\d+(?:\.\d+)?)\s*[- ]?(hour|hr|day)s?\s+grace",
    re.I)
_FLOOR = re.compile(
    r"(?:not\s+(?:to\s+)?exceed|no\s+more\s+than|maximum(?:\s+deduction)?(?:\s+of)?|"
    r"minimum(?:\s+(?:score|grade|percent))?(?:\s+of)?)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:percent|%)",
    re.I)
_MAX_DAYS = re.compile(
    r"(?:up\s+to|after|beyond|more\s+than|longer\s+than|past)\s+"
    r"(\d+)\s+days?|"
    r"(?:up\s+to|after)\s+(?:one|1|a)\s+week",
    re.I)


def empty(reason: str = "") -> dict:
    return {
        "kind": "none",
        "percent": 0.0,
        "interval": "day",
        "grace_hours": 0.0,
        "floor_percent": 0.0,
        "max_days": None,
        "canvas_applies": False,
        "summary": reason or "No late-work rule found in the syllabus.",
        "passage": "",
        "source": "",
    }


def extract_late_passages(text: str, window: int = 700) -> str:
    """The late-work section of a long syllabus, not the opening."""
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain:
        return ""
    spans: list[tuple[int, int]] = []
    for match in LATE_HINT.finditer(plain):
        start = max(0, match.start() - window)
        end = min(len(plain), match.end() + window)
        if start > 0:
            dot = plain.rfind(". ", start, match.start())
            if dot != -1:
                start = dot + 2
        if end < len(plain):
            dot = plain.find(". ", match.end(), end)
            if dot != -1:
                end = dot + 1
        spans.append((start, end))
    if not spans:
        return ""
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        pa, pb = merged[-1]
        if a <= pb + 40:
            merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
    return "\n\n".join(plain[a:b].strip() for a, b in merged)[:4000]


def _num(match, *groups) -> float:
    for g in groups:
        raw = match.group(g)
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    return 0.0


def _grace_hours(plain: str) -> float:
    match = _GRACE.search(plain)
    if not match:
        return 0.0
    amount = _num(match, 1, 3)
    unit = (match.group(2) or match.group(4) or "hour").lower()
    if unit.startswith("day"):
        return amount * 24.0
    return amount


def _floor_percent(plain: str) -> float:
    match = _FLOOR.search(plain)
    if not match:
        return 0.0
    return _num(match, 1)


def _max_days(plain: str) -> int | None:
    match = _MAX_DAYS.search(plain)
    if not match:
        return None
    if match.group(1):
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return 7  # "after one week"


def _percent_per(plain: str) -> tuple[float, str] | None:
    if _LETTER_PER_DAY.search(plain):
        return (10.0, "day")
    for rx in (_PER_INTERVAL, _PER_INTERVAL_FLIP):
        match = rx.search(plain)
        if match:
            pct = _num(match, 1)
            unit = (match.group(2) or "day").lower()
            if unit.startswith("hour"):
                return (pct, "hour")
            return (pct, "day")
    return None


def parse(text: str) -> dict:
    """Turn syllabus prose into a dock the auto-grader can apply."""
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain or not LATE_HINT.search(plain):
        return empty()
    out = empty()
    out["passage"] = extract_late_passages(plain) or plain[:1500]
    out["source"] = "syllabus"
    out["grace_hours"] = _grace_hours(plain)
    out["floor_percent"] = _floor_percent(plain)
    out["max_days"] = _max_days(plain)

    if _ACCEPTED_NO_PENALTY.search(plain) and not _percent_per(plain):
        out["kind"] = "no_penalty"
        out["summary"] = "Late work is accepted without a penalty, from the syllabus."
        return out

    per = _percent_per(plain)
    if per:
        percent, interval = per
        out["kind"] = "percent_per_hour" if interval == "hour" else "percent_per_day"
        out["percent"] = percent
        out["interval"] = interval
        bits = [f"{_fmt(percent)}% per {interval} late"]
        if out["grace_hours"]:
            bits.append(f"{_fmt(out['grace_hours'])} hour grace")
        if out["max_days"]:
            bits.append(f"zero after {out['max_days']} days")
        if out["floor_percent"]:
            bits.append(f"not below { _fmt(out['floor_percent'])}%")
        out["summary"] = ", ".join(bits) + ", from the syllabus."
        return out

    half = _HALF_CREDIT.search(plain)
    flat = _FLAT_PERCENT.search(plain)
    if half or flat:
        if half:
            percent = 50.0
        else:
            percent = _num(flat, 1)
            # "50% credit" means they keep half; "50% penalty" means they lose half.
            if "credit" in flat.group(0).lower() and percent <= 100:
                percent = 100.0 - percent
        out["kind"] = "flat_percent"
        out["percent"] = percent
        out["summary"] = f"{_fmt(percent)}% off if late, from the syllabus."
        return out

    if _NONE.search(plain):
        out["kind"] = "none_accepted"
        out["summary"] = "Late work is not accepted, from the syllabus."
        return out

    out["kind"] = "unknown"
    out["summary"] = "The syllabus mentions late work, but the rate was not clear."
    return out


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def intervals(seconds_late: int | float, policy: dict) -> int:
    """How many late steps Canvas-style: round up, after any grace."""
    seconds = max(0, int(seconds_late or 0) - int(float(policy.get("grace_hours") or 0) * 3600))
    if seconds <= 0:
        return 0
    step = 3600 if (policy.get("interval") or "day") == "hour" else 86400
    return (seconds + step - 1) // step


def deduction(entry: dict, earned: float) -> float:
    """Points to take off the earned score. 0 if nothing applies."""
    lp = (entry or {}).get("late_penalty") or {}
    if not lp.get("applied"):
        return 0.0
    earned = max(0.0, float(earned or 0))
    if lp.get("kind") == "none_accepted" or lp.get("past_window"):
        return round(earned, 2)
    percent = float(lp.get("percent") or 0)
    units = int(lp.get("units") or 0)
    kind = lp.get("kind") or ""
    if kind == "flat_percent":
        pct = percent
    elif kind in ("percent_per_day", "percent_per_hour", "canvas"):
        pct = percent * max(units, 0)
    else:
        return 0.0
    floor = float(lp.get("floor_percent") or 0)
    cap = max(0.0, 100.0 - floor)
    pct = min(max(pct, 0.0), cap)
    return round(earned * pct / 100.0, 2)


def attach(graded: dict, submission: dict, policy: dict | None,
           possible: float | None = None) -> dict:
    """Stamp a late_penalty on one auto-grade result. Does not change scores."""
    graded = dict(graded or {})
    policy = policy or {}
    if not submission or not submission.get("late"):
        return graded
    if graded.get("total") is None:
        return graded
    seconds = int(submission.get("seconds_late") or 0)
    units = intervals(seconds, policy)
    kind = policy.get("kind") or "none"
    lp = {
        "kind": kind,
        "seconds": seconds,
        "units": units,
        "percent": float(policy.get("percent") or 0),
        "interval": policy.get("interval") or "day",
        "grace_hours": float(policy.get("grace_hours") or 0),
        "floor_percent": float(policy.get("floor_percent") or 0),
        "max_days": policy.get("max_days"),
        "summary": policy.get("summary") or "",
        "source": policy.get("source") or "syllabus",
        "applied": False,
        "past_window": False,
    }
    if policy.get("canvas_applies"):
        lp["kind"] = "canvas"
        lp["summary"] = policy.get("summary") or (
            "Canvas already deducts for lateness; Studio will not dock twice.")
        graded["late_penalty"] = lp
        return graded
    if kind not in APPLY_KINDS:
        graded["late_penalty"] = lp
        return graded
    max_days = policy.get("max_days")
    if kind in ("percent_per_day", "percent_per_hour") and max_days is not None:
        step = 24 if kind == "percent_per_hour" else 1
        if units > int(max_days) * step:
            lp["past_window"] = True
            lp["kind"] = "none_accepted"
            lp["summary"] = f"Past the {max_days}-day window in the syllabus; score is zero."
    if units <= 0 and not lp["past_window"] and kind != "none_accepted":
        lp["summary"] = "Late, but inside the grace period. No deduction."
        graded["late_penalty"] = lp
        return graded
    if kind == "none_accepted" or lp["past_window"]:
        lp["applied"] = True
        lp["kind"] = "none_accepted"
        lp["points"] = round(float(graded.get("total") or 0), 2)
        lp["summary"] = lp["summary"] or "Late work is not accepted, from the syllabus."
        graded["late_penalty"] = lp
        flags = list(graded.get("flags") or [])
        flags.append("late penalty: not accepted")
        graded["flags"] = flags
        return graded
    lp["applied"] = True
    lp["points"] = deduction({"late_penalty": {**lp, "applied": True}},
                             float(graded.get("total") or 0))
    if kind == "flat_percent":
        lp["summary"] = f"{_fmt(lp['percent'])}% off for being late, from the syllabus."
    else:
        unit = "hour" if lp["interval"] == "hour" else "day"
        lp["summary"] = (
            f"{_fmt(lp['percent'])}% × {units} {unit}{'s' if units != 1 else ''} late"
            f" (−{ _fmt(lp['points'])} pts), from the syllabus.")
    flags = list(graded.get("flags") or [])
    flags.append("late penalty applied")
    graded["flags"] = flags
    graded["late_penalty"] = lp
    return graded


def refresh(entry: dict, earned: float) -> dict:
    """Recompute points after a hand edit of the earned score."""
    lp = dict((entry or {}).get("late_penalty") or {})
    if not lp:
        return lp
    lp["points"] = deduction({"late_penalty": lp}, earned)
    return lp


def load(client, course_id, plain_html: Callable[[str], str]) -> dict:
    """Syllabus (and Canvas, if it already deducts) for one course."""
    chunks: list[str] = []
    try:
        course = client.course_detail(course_id, include=["syllabus_body"])
        syl = plain_html(course.get("syllabus_body") or "")
        passage = extract_late_passages(syl)
        if passage:
            chunks.append(passage)
        elif LATE_HINT.search(syl or ""):
            chunks.append(syl[:2500])
    except Exception:  # noqa: BLE001
        pass
    try:
        pages = client.pages(course_id)
    except Exception:  # noqa: BLE001
        pages = []
    for page in pages or []:
        title = str(page.get("title") or "")
        if not SYLLABUS_TITLE.search(title) and not LATE_HINT.search(title):
            continue
        slug = page.get("url")
        try:
            full = client.page(course_id, slug) if slug else page
        except Exception:  # noqa: BLE001
            continue
        plain = plain_html(full.get("body") or "")
        passage = extract_late_passages(plain) or (
            plain[:2500] if SYLLABUS_TITLE.search(title) or LATE_HINT.search(title) else "")
        if passage:
            chunks.append(passage)
    parsed = parse("\n\n".join(chunks))

    canvas = None
    getter = getattr(client, "course_late_policy", None)
    try:
        if callable(getter):
            canvas = getter(course_id)
        elif hasattr(client, "get"):
            raw = client.get(f"/courses/{course_id}/late_policy")
            canvas = (raw or {}).get("late_policy") if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        canvas = None
    if isinstance(canvas, dict) and canvas.get("late_submission_deduction_enabled"):
        interval = str(canvas.get("late_submission_interval") or "day").lower()
        if interval not in ("day", "hour"):
            interval = "day"
        percent = float(canvas.get("late_submission_deduction") or 0)
        floor = 0.0
        if canvas.get("late_submission_minimum_percent_enabled"):
            floor = float(canvas.get("late_submission_minimum_percent") or 0)
        parsed["canvas_applies"] = True
        parsed["kind"] = "canvas"
        parsed["percent"] = percent
        parsed["interval"] = interval
        parsed["floor_percent"] = floor
        parsed["source"] = "canvas"
        parsed["summary"] = (
            f"Canvas already deducts {_fmt(percent)}% per {interval} late"
            + (f", not below {_fmt(floor)}%" if floor else "")
            + ". Studio will not dock twice.")
    return parsed
