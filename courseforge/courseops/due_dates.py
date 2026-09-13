"""Due-date rollover: lay the Week N modules onto the term calendar and write
one due date per week onto the assignments and quizzes in each module.

Ported from Compute-DueDates.ps1 and Set-DueDates.ps1. The rule (see
knowledge/academic-calendar.md, "DEFAULT due rule"):

- each instructional week's items are due the chosen weekday of the NEXT
  calendar week at the chosen time (Monday 23:59 by default);
- Week 1's due day is the first such weekday more than six days after the
  start, so a Thursday start and the following Monday start give one table;
- a calendar week that is entirely a break (Thanksgiving) is stepped over;
- a due day that is itself a holiday moves forward to the next class day;
- the last week (the final) is due on the finals-window end date.

Facts come from Canvas where they can: the start from the course (then the
term), the length from the highest "Week N" module, finals and breaks from the
calendar file. Anything missing is reported so the page can ask for it.

Dates are written as UTC instants. The page sends the instructor's timezone
(an IANA name and the JavaScript offset); the machine's own clock is the
fallback, so a local wall-clock 23:59 becomes the right UTC moment.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .. import ledger
from . import calendar as cal
from .common import (AREA, Gate, Log, area_dir, as_int, course_label, date_part,
                     load_json, now_iso, plural, quiet_log, save_json)

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEK_RE = re.compile(r"(?i)\bweek\s*(\d{1,2})\b")
START_HERE_RE = re.compile(r"(?i)^\s*start\s*here")
PLAN_FILE = "dates/plan.json"
APPLY_FILE = "dates/last-apply.json"


# ------------------------------------------------------------- the table
def compute(start, weeks: int, finals_end, breaks=None, weekday: str = "Monday",
            time: str = "23:59") -> list[dict]:
    """[{week, due_date, due_at, moved}] with due_at as a naive local
    'yyyy-mm-ddThh:mm:00'. `moved` says why a row is not on the plain weekday."""
    start_d = cal.parse_day(start)
    finals_d = cal.parse_day(finals_end)
    weeks = int(weeks)
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    if weekday not in WEEKDAYS:
        raise ValueError(f"weekday must be one of {', '.join(WEEKDAYS)}")
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(time).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"time must be hh:mm (got {time!r})")
    hour, minute = int(m.group(1)), int(m.group(2))

    holidays = cal.expand_breaks(breaks or [])
    target = WEEKDAYS.index(weekday)

    def is_holiday(d: date) -> bool:
        return d in holidays

    def full_break_week(monday: date) -> bool:
        return all(is_holiday(monday + timedelta(days=i)) for i in range(5))

    def week_start(d: date) -> date:
        return d - timedelta(days=d.weekday())

    def shift_past_holidays(d: date) -> date:
        # A due day that lands on a holiday moves to the next class day, and a
        # class day is Monday to Friday. When the chosen weekday is itself a
        # Saturday or Sunday the weekend is not a reason to move: otherwise
        # every "due Sunday" table quietly became "due Monday".
        weekend_is_off = target < 5
        while is_holiday(d) or (weekend_is_off and d.weekday() >= 5):
            d += timedelta(days=1)
        return d

    anchor = start_d + timedelta(days=7)
    while anchor.weekday() != target:
        anchor += timedelta(days=1)
    while (anchor - start_d).days <= 6:
        anchor += timedelta(days=7)

    rows = []
    ws = week_start(anchor) - timedelta(days=7)
    for n in range(1, weeks + 1):
        while full_break_week(ws):
            ws += timedelta(days=7)
        moved = ""
        if n == weeks:
            due_day = finals_d
            moved = "final: due on the last day of finals"
        else:
            nxt = ws + timedelta(days=7)
            skipped = False
            while full_break_week(nxt):
                nxt += timedelta(days=7)
                skipped = True
            plain = nxt + timedelta(days=target)
            due_day = shift_past_holidays(plain)
            if skipped:
                moved = "steps over a full break week"
            if due_day != plain:
                moved = (moved + "; " if moved else "") + \
                    f"moved off a holiday ({plain.isoformat()})"
        rows.append({
            "week": n,
            "due_date": due_day.isoformat(),
            "due_at": f"{due_day.isoformat()}T{hour:02d}:{minute:02d}:00",
            "moved": moved,
        })
        ws += timedelta(days=7)
    return rows


# --------------------------------------------------------------- timezones
def _machine_offset_min() -> int:
    """JavaScript-style offset for right now: minutes to add to local to get UTC."""
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    return -int(off.total_seconds() // 60)


def to_utc_iso(local_iso: str, tz_name: str | None = None, tz_offset_min=None) -> str:
    """A wall-clock time in the instructor's zone -> 'yyyy-mm-ddThh:mm:ssZ'.

    Prefers the IANA name (correct across the DST change in November). Without
    tz data, the machine's own clock is used when its offset matches the
    browser's; only then does a fixed offset stand in.
    """
    text = str(local_iso).strip()
    if text.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", text):
        aware = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    naive = datetime.fromisoformat(text)
    aware = None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            aware = naive.replace(tzinfo=ZoneInfo(str(tz_name)))
        except Exception:  # noqa: BLE001  (no tzdata on this machine, or a bad name)
            aware = None
    if aware is None and tz_offset_min not in (None, ""):
        offset = int(tz_offset_min)
        if offset == _machine_offset_min():
            aware = naive.astimezone()
        else:
            aware = naive.replace(tzinfo=timezone(timedelta(minutes=-offset)))
    if aware is None:
        aware = naive.astimezone()
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def same_instant(a: str | None, b: str | None) -> bool:
    if not a and not b:
        return True
    if not a or not b:
        return False
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        db = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except ValueError:
        return str(a) == str(b)
    if da.tzinfo is None:
        da = da.replace(tzinfo=timezone.utc)
    if db.tzinfo is None:
        db = db.replace(tzinfo=timezone.utc)
    return da == db


# ------------------------------------------------------------------ facts
def week_number(module_name: str) -> int | None:
    m = WEEK_RE.search(module_name or "")
    return int(m.group(1)) if m else None


def facts(app, course_id, modules: list[dict] | None = None) -> dict:
    """What can be derived without asking: start, weeks, term, finals, breaks."""
    c = app.content
    course = c.course_detail(course_id) or {}
    sources: dict[str, str] = {}
    missing: list[str] = []

    start = date_part(course.get("start_at"))
    if start:
        sources["start"] = "course start date in Canvas"
    else:
        try:
            with_term = c.course_detail(course_id, include=["term"]) or {}
            start = date_part((with_term.get("term") or {}).get("start_at"))
        except Exception:  # noqa: BLE001
            start = None
        if start:
            sources["start"] = "term start date in Canvas (the course has none)"
    if not start:
        missing.append("start")

    if modules is None:
        modules = c.modules(course_id, include_items=True)
    weeks = 0
    for m in modules:
        n = week_number(m.get("name") or "")
        if n and n > weeks:
            weeks = n
    if weeks:
        sources["weeks"] = f"highest Week N module ({weeks})"
    else:
        missing.append("weeks")

    term = finals_end = None
    breaks: list[str] = []
    if start:
        term = cal.term_name(start)
        found = None
        try:
            found = cal.lookup(start)
        except (OSError, ValueError):
            found = None
        if found:
            finals_end, breaks = found["finals_end"], found["breaks"]
            sources["finals_end"] = sources["breaks"] = f"{term} in the academic calendar"
        else:
            sources["finals_end"] = sources["breaks"] = f"{term} is not in the academic calendar"
    if not finals_end:
        missing.append("finals_end")

    return {
        "course": {"id": course.get("id", course_id), "name": course.get("name"),
                   "code": course.get("course_code"),
                   "end_at": course.get("end_at")},
        "start": start, "weeks": weeks or None, "term": term,
        "finals_end": finals_end, "breaks": breaks,
        "weekday": "Monday", "time": "23:59",
        "sources": sources, "missing": missing,
        "known_terms": cal.known_terms(),
    }


def _apply_overrides(f: dict, overrides: dict | None) -> dict:
    o = overrides or {}
    out = dict(f)
    if o.get("start"):
        out["start"] = cal.parse_day(o["start"]).isoformat()
        out["sources"] = {**out["sources"], "start": "typed in"}
    if o.get("weeks"):
        out["weeks"] = int(o["weeks"])
        out["sources"] = {**out["sources"], "weeks": "typed in"}
    if o.get("finals_end"):
        out["finals_end"] = cal.parse_day(o["finals_end"]).isoformat()
        out["sources"] = {**out["sources"], "finals_end": "typed in"}
    if "breaks" in o and o["breaks"] is not None:
        b = o["breaks"]
        out["breaks"] = cal.parse_breaks_text(b) if isinstance(b, str) else [str(x) for x in b]
        out["sources"] = {**out["sources"], "breaks": "typed in"}
    if o.get("term"):
        out["term"] = str(o["term"])
    if o.get("weekday"):
        out["weekday"] = str(o["weekday"]).capitalize()
    if o.get("time"):
        out["time"] = str(o["time"])
    if out.get("start") and out.get("term") in (None, "") :
        out["term"] = cal.term_name(out["start"])
    out["missing"] = [k for k in ("start", "weeks", "finals_end") if not out.get(k)]
    return out


# ------------------------------------------------------------------- plan
def plan(app, course_id, overrides: dict | None = None, tz_name: str | None = None,
         tz_offset_min=None) -> dict:
    """The week table: per Week N module, its dated items, current and proposed
    due dates, and a note. Nothing is written."""
    c = app.content
    modules = c.modules(course_id, include_items=True)
    f = _apply_overrides(facts(app, course_id, modules), overrides)
    out = {"facts": f, "rows": [], "table": [], "writes": 0, "tz_name": tz_name,
           "tz_offset_min": tz_offset_min, "computed_at": now_iso()}
    if f["missing"]:
        out["note"] = "Tell me the missing facts above and the table is computed from them."
        save_json(area_dir(app, course_id) / PLAN_FILE, out)
        return out

    table = compute(f["start"], f["weeks"], f["finals_end"], f["breaks"], f["weekday"], f["time"])
    by_week = {r["week"]: r for r in table}
    out["table"] = table

    assignments = {str(a["id"]): a for a in c.assignments_content(course_id)}
    quizzes = {str(q["id"]): q for q in c.quizzes_content(course_id)}
    seen: set[tuple[str, str]] = set()
    rows = []
    for m in sorted(modules, key=lambda x: (x.get("position") or 0, x.get("id") or 0)):
        name = m.get("name") or ""
        base = {"module_id": m.get("id"), "module": name, "items": []}
        if START_HERE_RE.match(name):
            rows.append({**base, "week": None, "note": "Start Here is skipped", "due_local": None,
                         "due_utc": None})
            continue
        wk = week_number(name)
        if not wk:
            continue
        due = by_week.get(wk)
        items = m.get("items")
        if items is None:
            items = c.module_items(course_id, m["id"])
        found = []
        for it in items or []:
            kind, ident, cur, title = None, None, None, it.get("title")
            typ = it.get("type")
            cid_ = str(it.get("content_id") or "")
            if typ == "Assignment":
                a = assignments.get(cid_)
                if a and a.get("quiz_id"):
                    q = quizzes.get(str(a["quiz_id"]))
                    kind, ident = "quiz", str(a["quiz_id"])
                    cur = (q or {}).get("due_at") or a.get("due_at")
                    title = (q or {}).get("title") or a.get("name") or title
                else:
                    kind, ident = "assignment", cid_
                    cur = (a or {}).get("due_at")
                    title = (a or {}).get("name") or title
            elif typ == "Quiz":
                q = quizzes.get(cid_)
                kind, ident = "quiz", cid_
                cur = (q or {}).get("due_at")
                title = (q or {}).get("title") or title
            else:
                continue
            if not ident:
                continue
            key = (kind, ident)
            if key in seen:
                found.append({"kind": kind, "id": ident, "name": title, "current": cur,
                              "skip": "also in an earlier module; dated there"})
                continue
            seen.add(key)
            found.append({"kind": kind, "id": ident, "name": title, "current": cur})
        due_utc = to_utc_iso(due["due_at"], tz_name, tz_offset_min) if due else None
        notes = []
        if not due:
            notes.append(f"no computed date for week {wk}")
        elif due.get("moved"):
            notes.append(due["moved"])
        live = [x for x in found if not x.get("skip")]
        if not live:
            notes.append("no assignments or quizzes in this module")
        elif due and all(same_instant(x.get("current"), due_utc) for x in live):
            notes.append("already on this date")
        rows.append({**base, "week": wk, "items": found,
                     "due_local": due["due_at"] if due else None, "due_utc": due_utc,
                     "note": "; ".join(notes)})
    out["rows"] = rows
    out["writes"] = sum(1 for r in rows for x in r["items"]
                        if not x.get("skip") and r.get("due_utc")
                        and not same_instant(x.get("current"), r["due_utc"]))
    save_json(area_dir(app, course_id) / PLAN_FILE, out)
    return out


# ------------------------------------------------------------------ apply
def build_writes(rows: list[dict], tz_name: str | None = None, tz_offset_min=None) -> list[dict]:
    """Rows from the page (edited proposed dates) -> [{kind, id, name, week, from, to}].
    An item may carry its own `to` (used by the ledger's roll back); otherwise
    the row's `due_local` is converted."""
    writes = []
    for row in rows or []:
        row_to = None
        if row.get("due_local"):
            row_to = to_utc_iso(row["due_local"], tz_name, tz_offset_min)
        elif row.get("due_utc"):
            row_to = to_utc_iso(row["due_utc"])
        for it in row.get("items") or []:
            if it.get("skip"):
                continue
            to = it.get("to")
            to = to_utc_iso(to) if to else row_to
            if not to or it.get("kind") not in ("assignment", "quiz") or not it.get("id"):
                continue
            if same_instant(it.get("current"), to):
                continue
            writes.append({"kind": it["kind"], "id": str(it["id"]), "name": it.get("name") or "",
                           "week": row.get("week"), "from": it.get("current"), "to": to})
    return writes


def apply(app, course_id, rows: list[dict], gate: Gate, log: Log = quiet_log,
          tz_name: str | None = None, tz_offset_min=None) -> dict:
    """Write the due dates, read each back, compare, record in the ledger."""
    c = app.content
    course = c.course_detail(course_id) or {}
    label = course_label(course, course_id)
    writes = build_writes(rows, tz_name, tz_offset_min)
    if not writes:
        return {"written": 0, "results": [], "note": "Every item already has its proposed date; nothing to write."}

    na = sum(1 for w in writes if w["kind"] == "assignment")
    nq = sum(1 for w in writes if w["kind"] == "quiz")
    parts = []
    if na:
        parts.append(plural(na, "assignment"))
    if nq:
        parts.append(plural(nq, "quiz", "quizzes"))
    weeks = sorted({w["week"] for w in writes if w.get("week")})
    span = f" for weeks {weeks[0]} to {weeks[-1]}" if len(weeks) > 1 else (f" for week {weeks[0]}" if weeks else "")
    sentence = (f"Set the due date on {' and '.join(parts)} in {label}{span}. "
                "Nothing is deleted and publish state is not changed. "
                "The previous dates are kept, so this can be rolled back.")
    detail = [{"label": f"Week {w['week']}: {w['name']}" if w.get("week") else w["name"],
               "from": w["from"] or "no due date", "to": w["to"]} for w in writes]
    payload = {"course_id": str(course_id),
               "writes": [{"kind": w["kind"], "id": w["id"], "to": w["to"]} for w in writes]}
    gate("courseops.dates", payload, sentence, detail)

    results = []
    ok = 0
    for i, w in enumerate(writes, 1):
        log(f"{w['kind']} {w['name']}: due {w['to']}", i, len(writes))
        try:
            if w["kind"] == "assignment":
                c.update_assignment_content(course_id, w["id"], due_at=w["to"])
                back = c.get(f"/courses/{course_id}/assignments/{w['id']}") or {}
                got = back.get("due_at")
            else:
                echo = c.update_quiz_content(course_id, w["id"], due_at=w["to"]) or {}
                # An unpublished quiz object is stale on GET (gotcha 3); the
                # PUT echo is what Canvas stored. Published ones read back true.
                if echo.get("published"):
                    back = c.quiz_content(course_id, w["id"]) or {}
                    got = back.get("due_at")
                else:
                    got = echo.get("due_at")
            good = same_instant(got, w["to"])
            ok += 1 if good else 0
            results.append({**w, "ok": good, "got": got,
                            "note": "" if good else "Canvas answered but the date read back differs"})
        except Exception as exc:  # noqa: BLE001
            results.append({**w, "ok": False, "got": None, "note": f"{type(exc).__name__}: {exc}"})
    undo_rows = [{"week": w["week"], "items": [{"kind": w["kind"], "id": w["id"], "name": w["name"],
                                                 "current": w["to"], "to": w["from"]}]}
                 for w in results if w["ok"] and w.get("from")]
    ledger.record(app.course_dir(course_id), AREA,
                  f"Set due dates on {plural(ok, 'item')} in {label}" +
                  (f" (weeks {weeks[0]} to {weeks[-1]})" if len(weeks) > 1 else ""),
                  url=f"{app.cfg.base_url}/courses/{course_id}/assignments", count=ok, kind="dates",
                  undo={"route": f"/tools/{course_id}/dates/apply", "body": {"rows": undo_rows}}
                  if undo_rows else None)
    out = {"written": ok, "failed": len(results) - ok, "results": results, "at": now_iso()}
    save_json(area_dir(app, course_id) / APPLY_FILE, out)
    return out


def last_plan(app, course_id) -> dict | None:
    return load_json(area_dir(app, course_id) / PLAN_FILE)


def last_apply(app, course_id) -> dict | None:
    return load_json(area_dir(app, course_id) / APPLY_FILE)


def table_text(rows: list[dict]) -> str:
    """The week table for a terminal."""
    lines = [f"{'Week':>4}  {'Module':<34} {'Proposed due':<20} {'Items':>5}  Note"]
    for r in rows:
        wk = r.get("week")
        lines.append(f"{(wk if wk is not None else '-'):>4}  {(r.get('module') or '')[:34]:<34} "
                     f"{(r.get('due_local') or '-'):<20} {len([x for x in r.get('items') or [] if not x.get('skip')]):>5}  "
                     f"{r.get('note') or ''}")
    return "\n".join(lines)


__all__ = ["compute", "facts", "plan", "apply", "build_writes", "to_utc_iso", "same_instant",
           "week_number", "table_text", "last_plan", "last_apply", "WEEKDAYS"]
