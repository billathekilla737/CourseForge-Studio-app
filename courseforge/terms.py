"""Work out which academic term a course belongs to.

Canvas's own term objects are not dependable here: start_at comes back null,
end_at is often a year off (a Fall 2026 term reporting an end of 2027-12-11),
and the same term id can arrive with different dates depending on the
enrollment it was read through.

The course code prefix is reliable and already sorts correctly. MGCCC and most
Colleague/Banner schools use YYYYTT:

    202610  ->  Spring 2026
    202620  ->  Summer 2026
    202630  ->  Fall 2026

We parse that when it is present and fall back to the Canvas term name, so
sandboxes and non-standard shells still land somewhere sensible.
"""
from __future__ import annotations

import re
from datetime import date

# Trailing two digits of the term code -> season name and calendar order.
SEASONS: dict[str, tuple[str, int]] = {
    "10": ("Spring", 1),
    "20": ("Summer", 2),
    "30": ("Fall", 3),
    "40": ("Winter", 4),
}

_CODE = re.compile(r"\b(20\d{2})(10|20|30|40)\b")

OTHER_LABEL = "Sandboxes and other"
OTHER_SORT = -1


def parse_code(text: str) -> str | None:
    """Pull a YYYYTT term code out of a course name or code."""
    match = _CODE.search(text or "")
    return match.group(0) if match else None


def label_for(code: str) -> str:
    year, season = code[:4], code[4:6]
    name = SEASONS.get(season, ("Term " + season, 9))[0]
    return f"{name} {year}"


def current_code(today: date | None = None) -> str:
    """The term we are in right now, from the calendar.

    Spring runs January to May, Summer June and July, Fall August to December.
    """
    today = today or date.today()
    if today.month <= 5:
        season = "10"
    elif today.month <= 7:
        season = "20"
    else:
        season = "30"
    return f"{today.year}{season}"


def term_of(course: dict) -> dict:
    """Return {code, label, sort} for one course."""
    haystack = f"{course.get('course_code','')} {course.get('name','')}"
    code = parse_code(haystack)
    if code:
        return {"code": code, "label": label_for(code), "sort": int(code)}

    # No code in the name. Fall back to the Canvas term name if it looks real.
    name = ((course.get("term") or {}).get("name") or "").strip()
    if name and name.lower() not in ("default term", ""):
        guessed = parse_code(name)
        if guessed:
            return {"code": guessed, "label": label_for(guessed), "sort": int(guessed)}
        # Something like "Fall 2026 (MSVCC)" with no numeric code.
        year = re.search(r"\b(20\d{2})\b", name)
        for season, (word, _order) in SEASONS.items():
            if word.lower() in name.lower() and year:
                code = f"{year.group(1)}{season}"
                return {"code": code, "label": label_for(code), "sort": int(code)}
        return {"code": None, "label": name, "sort": OTHER_SORT}

    return {"code": None, "label": OTHER_LABEL, "sort": OTHER_SORT}


def summarize(courses: list[dict]) -> dict:
    """Group courses by term and pick a sensible default selection.

    Returns {terms: [{code,label,sort,count}], default: <code or label>}.
    Terms come back newest first so the current one sits at the top of a picker.
    """
    buckets: dict[str, dict] = {}
    for course in courses:
        term = course.get("term_label") or OTHER_LABEL
        entry = buckets.setdefault(term, {
            "label": term,
            "code": course.get("term_code"),
            "sort": course.get("term_sort", OTHER_SORT),
            "count": 0,
        })
        entry["count"] += 1

    terms = sorted(buckets.values(), key=lambda t: (t["sort"], t["label"]), reverse=True)

    now = current_code()
    default = next((t["label"] for t in terms if t["code"] == now), None)
    if default is None:
        real = [t for t in terms if t["sort"] != OTHER_SORT]
        default = real[0]["label"] if real else (terms[0]["label"] if terms else "")
    return {"terms": terms, "default": default, "current_code": now}
