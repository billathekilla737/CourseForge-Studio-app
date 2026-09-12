"""The term calendar: finals end and breaks per term, read from the fenced
json block in knowledge/academic-calendar.md.

Ported from Get-TermCalendar.ps1. The term is inferred from the start month
(8 and later is Fall, 1 to 4 is Spring, 5 to 7 is Summer). A break entry is a
single day 'yyyy-mm-dd' or a range 'yyyy-mm-dd..yyyy-mm-dd'. A term missing
from the table returns None and the caller asks the instructor.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

KNOWLEDGE_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "academic-calendar.md"
_FENCE = re.compile(r"(?ms)^```json[ \t]*\r?\n(.*?)^```")


def parse_day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not m:
        raise ValueError(f"not a yyyy-mm-dd date: {value!r}")
    return date.fromisoformat(m.group(1))


def term_name(start) -> str:
    d = parse_day(start)
    if d.month >= 8:
        return f"Fall {d.year}"
    if d.month <= 4:
        return f"Spring {d.year}"
    return f"Summer {d.year}"


def read_table(path: Path | str | None = None) -> dict:
    """The first fenced json block in the calendar file, parsed."""
    path = Path(path) if path else KNOWLEDGE_FILE
    if not path.is_file():
        raise FileNotFoundError(f"academic calendar not found at {path}")
    text = path.read_text(encoding="utf-8-sig")
    m = _FENCE.search(text)
    if not m:
        raise ValueError(f"no fenced json block in {path}")
    table = json.loads(m.group(1))
    if not isinstance(table, dict):
        raise ValueError("the calendar json block is not an object keyed by term")
    return table


def expand_breaks(breaks) -> set[date]:
    """['2026-09-07', '2026-10-12..2026-10-13'] -> every day named, as dates."""
    out: set[date] = set()
    for entry in breaks or []:
        if entry is None:
            continue
        text = str(entry).strip()
        if not text:
            continue
        if ".." in text:
            a, b = (p.strip() for p in text.split("..", 1))
            d0, d1 = parse_day(a), parse_day(b)
            if d1 < d0:
                d0, d1 = d1, d0
            d = d0
            while d <= d1:
                out.add(d)
                d += timedelta(days=1)
        else:
            out.add(parse_day(text))
    return out


def parse_breaks_text(text: str) -> list[str]:
    """What a person types into the breaks box: entries split on commas,
    semicolons or newlines, each a day or an a..b range."""
    parts = re.split(r"[,\n;]+", text or "")
    out = []
    for p in parts:
        p = p.strip().strip("'\"")
        if not p:
            continue
        # tolerate 'a - b' and 'a to b' as range spellings
        p = re.sub(r"\s*(?:\.\.|\s-\s|\bto\b)\s*", "..", p)
        out.append(p)
    expand_breaks(out)          # validates; raises ValueError on a bad entry
    return out


def lookup(start, path: Path | str | None = None) -> dict | None:
    """{'term', 'finals_end', 'breaks'} for the term the start date falls in,
    or None when the table does not have that term."""
    term = term_name(start)
    table = read_table(path)
    entry = table.get(term)
    if not entry:
        return None
    return {
        "term": term,
        "finals_end": str(entry.get("finalsEnd") or entry.get("finals_end") or ""),
        "breaks": [str(b) for b in (entry.get("breaks") or [])],
    }


def known_terms(path: Path | str | None = None) -> list[str]:
    try:
        return sorted(read_table(path).keys())
    except (OSError, ValueError):
        return []
