"""Campus breaks from the college academic calendar.

The page is https://mgccc.edu/about/academic-calendar/. A holiday, or a
break that says it begins and ends, is a day with no class. Registration,
grades, and the start of a term are not. If the site does not answer, the
last copy saved on this computer is used, then the snapshot in the
knowledge file.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser

from . import calendar as cal

SOURCE = "https://mgccc.edu/about/academic-calendar/"
CACHE_NAME = "school-calendar.json"
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_DATE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2})(?:\s*-\s*\d{1,2})?,?\s+(\d{4})",
    re.I,
)
_HEAD = re.compile(
    r"^(Fall|Spring|Summer)\s+(\d{4})\s+Academic Schedule\s*$", re.I)
_BEGIN = re.compile(r"\b(?:BEGINS|BEGIN)\b", re.I)
_END = re.compile(r"\b(?:ENDS|END)\b", re.I)


class _Rows(HTMLParser):
    """Headings and table rows, as plain text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, list[tuple[str, str]]]] = []
        self._heading = ""
        self._in_head = False
        self._head_text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._table_rows: list[tuple[str, str]] = []
        self._in_table = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("h2", "h3"):
            self._flush_table()
            self._in_head = True
            self._head_text = []
        elif tag == "table":
            self._in_table = True
            self._table_rows = []
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("h2", "h3") and self._in_head:
            self._heading = re.sub(r"\s+", " ", "".join(self._head_text)).strip()
            self._in_head = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if len(self._row) >= 2 and self._row[0] and self._row[1]:
                self._table_rows.append((self._row[0], self._row[1]))
            self._row = None
        elif tag == "table":
            self._flush_table()

    def handle_data(self, data):
        if self._in_head:
            self._head_text.append(data)
        elif self._cell is not None:
            self._cell.append(data)

    def _flush_table(self):
        if self._table_rows:
            self.sections.append((self._heading, list(self._table_rows)))
        self._table_rows = []
        self._in_table = False

    def close(self):
        super().close()
        self._flush_table()


def _day(text: str) -> date | None:
    match = _DATE.search(text or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def _kind(subject: str) -> str:
    """'day', 'begin', 'end', or '' when this row is not a campus break."""
    text = re.split(r"[;(]", subject or "", maxsplit=1)[0]
    low = text.lower()
    if any(word in low for word in (
        "classes begin", "courses begin", "courses start", "classes start",
        "registration", "advising", "exam", "grades due", "midterm",
        "residence", "convocation", "workshop", "orientation",
    )):
        return ""
    if re.search(r"\bholiday\b", text, re.I) and "break" not in low:
        return "day"
    if re.search(r"\b(?:break|holidays)\b", text, re.I):
        if _BEGIN.search(text):
            return "begin"
        if _END.search(text):
            return "end"
    return ""


def _name(subject: str) -> str:
    text = re.split(r"[;(]", subject or "", maxsplit=1)[0]
    text = re.sub(r"\b(?:BEGINS|BEGIN|ENDS|END|holidays|holiday)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    if text.lower() in ("spring", "fall", "summer", "winter"):
        text += " break"
    return text or "Campus break"


def parse_calendar(html: str) -> list[dict]:
    """Every no-class day on the college page, with the break's name."""
    parser = _Rows()
    parser.feed(html or "")
    parser.close()
    found: dict[tuple[str, str], str] = {}
    for heading, rows in parser.sections:
        match = _HEAD.match(heading or "")
        if not match:
            continue
        term = f"{match.group(1).title()} {match.group(2)}"
        open_ranges: list[tuple[str, date]] = []
        for when, subject in rows:
            kind = _kind(subject)
            if not kind:
                continue
            day = _day(when)
            if not day:
                continue
            name = _name(subject)
            if kind == "day":
                found[(term, day.isoformat())] = name
            elif kind == "begin":
                open_ranges.append((name, day))
            else:
                slot = None
                for index, (open_name, _start) in enumerate(open_ranges):
                    if open_name.lower() == name.lower():
                        slot = index
                        break
                if slot is None and len(open_ranges) == 1:
                    slot = 0
                if slot is None:
                    found[(term, day.isoformat())] = name
                    continue
                open_name, start = open_ranges.pop(slot)
                cursor = start
                last = day if day >= start else start
                if day < start:
                    cursor = day
                while cursor <= last:
                    found[(term, cursor.isoformat())] = open_name
                    cursor += timedelta(days=1)
        for open_name, start in open_ranges:
            found[(term, start.isoformat())] = open_name
    return [
        {"term": term, "date": iso, "name": found[(term, iso)]}
        for term, iso in sorted(found)
    ]


def fetch_calendar(timeout: float = 12) -> str:
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "CourseForgeStudio"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


def _cache_path(app):
    return app.store.root / CACHE_NAME


def _read_cache(app) -> list[dict]:
    try:
        raw = json.loads(_cache_path(app).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return []
    days = raw.get("days") if isinstance(raw, dict) else None
    return [row for row in days or [] if isinstance(row, dict) and row.get("date")]


def _write_cache(app, days: list[dict]) -> None:
    try:
        path = _cache_path(app)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "source": SOURCE,
            "days": days,
        }, indent=2), encoding="utf-8")
    except (OSError, AttributeError):
        pass


def _from_knowledge(term: str) -> list[dict]:
    try:
        table = cal.read_table()
    except (OSError, ValueError):
        return []
    entry = table.get(term) or {}
    try:
        days = cal.expand_breaks(entry.get("breaks") or [])
    except ValueError:
        return []
    return [{"term": term, "date": day.isoformat(), "name": "Campus break"} for day in sorted(days)]


def _term_of(label: str) -> str:
    match = re.search(r"(Fall|Spring|Summer)\s+(\d{4})", label or "", re.I)
    if not match:
        return (label or "").strip()
    return f"{match.group(1).title()} {match.group(2)}"


def for_term(app, term: str) -> dict:
    """Breaks for one term. The college page wins; a saved copy is the backup."""
    wanted = _term_of(term)
    live = False
    days: list[dict] = []
    try:
        days = parse_calendar(fetch_calendar())
        if days:
            _write_cache(app, days)
            live = True
    except Exception:  # noqa: BLE001
        days = []
    if not days:
        days = _read_cache(app)
    used_knowledge = False
    picked = [row for row in days if _term_of(row.get("term") or "") == wanted]
    if not picked:
        picked = _from_knowledge(wanted)
        used_knowledge = bool(picked)
    if live:
        note = "Campus breaks are from the college academic calendar."
    elif used_knowledge:
        note = "The college site did not answer. These breaks are the saved copy on this computer."
    elif picked:
        note = "The college site did not answer. Showing the last copy saved from it."
    else:
        note = "No campus breaks are on file for this term."
    return {
        "term": wanted,
        "source": SOURCE,
        "live": live,
        "days": [{"date": row["date"], "name": row.get("name") or "Campus break"}
                 for row in picked],
        "note": note,
    }
