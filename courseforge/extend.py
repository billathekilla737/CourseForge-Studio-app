"""Deadline extensions: a student was ill, so their dates move and nobody else's.

A student is in hospital for a week, or loses a parent. What they need is not a
changed assignment -- the rest of the class is on time -- but a different due
date for themselves, on the handful of things that fell during the absence,
in every course they are in. Doing that by hand means opening each assignment
in each course, adding an override, copying the date, adding the days, and
remembering to move the lock date too or the new due date does nothing.

This module is the arithmetic and the judgement, with no Canvas I/O in it, so
it can be tested without a course. The server does the reading and writing.

Three things it is careful about.

**It extends from the date that actually applies to the student.** Canvas can
hold several dates for one assignment: the class date, a section's date, and a
per-student override from an earlier extension. The student sees whichever is
most lenient, so that is the one +3 days is measured from. Starting from the
class date instead would quietly pull a student's deadline *backwards* when
they already had longer, which is the one outcome nobody would ever intend.

**It moves the lock date with the due date.** An assignment that locks on the
due date will still refuse the submission at the new one, so the extension
would be a date change that changes nothing. The lock moves by the same number
of days, keeping whatever gap the instructor set.

**It gives each student their own override.** Canvas allows a student into only
one ad-hoc override per assignment, so sharing one between students makes the
next extension for any of them a conflict. One override per student per
assignment costs an extra write and stays composable: a second extension for
the same person on the same assignment updates the override already there
instead of colliding with it.

What it will not do: touch an override it did not make that holds other
students as well. Rewriting that would move dates for people nobody selected.
Those rows are reported and left alone.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A ceiling on the shift. Not a Canvas limit -- a typo guard. An extension
# longer than this is a withdrawal or an incomplete, and neither is a due date.
MAX_DAYS = 90
# A ceiling on one run, for the same reason: extending 900 dates in one press
# is not an accommodation, it is a rollover, and courseops does those.
MAX_ROWS = 400

# The title every override this tool creates carries. It is how a later run
# recognises its own work, so it must stay stable; the student's name is on the
# end so Canvas's own assignment page says who it is for.
TITLE_PREFIX = "Extension"


def title_for(name: str, user_id) -> str:
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return f"{TITLE_PREFIX}: {clean or 'student ' + str(user_id)}"


def is_ours(title: str) -> bool:
    return str(title or "").strip().lower().startswith(TITLE_PREFIX.lower() + ":")


# ------------------------------------------------------------------- dates
def parse_iso(value) -> datetime | None:
    """Canvas hands back '2026-09-09T04:59:00Z'. Anything else is None."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _zone(tz_name: str | None):
    """The course's timezone, or None meaning "use this machine's".

    Windows ships no IANA database, so `ZoneInfo("America/Chicago")` raises
    here unless the `tzdata` package is installed. The old version of this
    swallowed that and fell back to plain UTC arithmetic, which is wrong twice
    over: it puts an 11:59 pm deadline in the wrong calendar day when deciding
    what is inside the absence window, and it shifts the time by an hour across
    a daylight-saving boundary. Falling back to the machine's own zone is right
    whenever the instructor and the course are in the same one, which is the
    ordinary case, and `zone_note` says out loud when that is what happened.
    """
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(tz_name))
    except Exception:  # noqa: BLE001  (no database, or a name Canvas made up)
        return None


def zone_note(tz_name: str | None) -> str:
    """Which zone the arithmetic actually used, for the page to show."""
    if tz_name and _zone(tz_name) is not None:
        return str(tz_name)
    local = datetime.now().astimezone().tzname() or "this computer's clock"
    if tz_name:
        return (f"{local} (this PC) — Canvas says the course is in {tz_name}, "
                f"but this machine has no timezone database to read it with. "
                f"Install the tzdata package to use the course's own zone.")
    return f"{local} (this PC)"


def _as_local(when: datetime, zone):
    return when.astimezone(zone) if zone is not None else when.astimezone()


def shift(value, days: int, tz_name: str | None = None) -> str | None:
    """Move an instant by whole days, keeping the wall-clock time.

    Done in the course's own timezone on purpose. A deadline of 11:59 pm that
    steps over the end of daylight saving is still meant to be 11:59 pm; adding
    72 hours to the UTC instant would make it 10:59 pm and start marking work
    late an hour early.
    """
    when = parse_iso(value)
    if when is None:
        return None
    zone = _zone(tz_name)
    naive = (_as_local(when, zone) + timedelta(days=int(days))).replace(tzinfo=None)
    # Re-attaching the zone to the moved wall-clock time is what resolves the
    # new offset; `astimezone()` on a naive value does the same against the
    # machine's zone, DST included.
    moved = naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()
    return moved.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def touches_window(assignment: dict, overrides: list[dict], user_ids,
                   sections: dict, start, end, tz_name: str | None = None) -> bool:
    """True if the class date or any selected student's effective date is in the window."""
    if day_in_window(assignment.get("due_at"), start, end, tz_name):
        return True
    for uid in user_ids:
        eff = effective(assignment, overrides, uid, sections.get(str(uid)) or [])
        if day_in_window(eff.get("due_at"), start, end, tz_name):
            return True
    return False


def day_in_window(value, start, end, tz_name: str | None = None) -> bool:
    """Is this instant's local calendar day inside [start, end]?

    Compared as days rather than instants because the window is typed as two
    dates. An 11:59 pm deadline on the last day of the absence is inside it,
    which comparing against midnight would get wrong.
    """
    when = parse_iso(value)
    if when is None:
        return False
    day = _as_local(when, _zone(tz_name)).date()
    if start and day < _day(start):
        return False
    if end and day > _day(end):
        return False
    return True


def _day(value):
    if hasattr(value, "year") and not hasattr(value, "hour"):
        return value
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value).strip())
    if not m:
        raise ValueError(f"{value!r} is not a date (yyyy-mm-dd)")
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()


def pretty(value, tz_name: str | None = None) -> str:
    """'Tue 9 Sep, 11:59 pm' -- for a plan somebody has to read and agree to."""
    when = parse_iso(value)
    if when is None:
        return "no date"
    local = _as_local(when, _zone(tz_name))
    hour = local.strftime("%I").lstrip("0") or "12"
    return (f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, "
            f"{hour}:{local.strftime('%M %p').lower()}")


# ------------------------------------------------- which date the student sees
def applicable(overrides: list[dict], user_id, section_ids) -> list[dict]:
    """Every override that governs this student on this assignment."""
    uid = str(user_id)
    sections = {str(s) for s in (section_ids or [])}
    out = []
    for o in overrides or []:
        ids = {str(u) for u in (o.get("student_ids") or [])}
        if uid in ids:
            out.append({**o, "how": "adhoc"})
        elif o.get("course_section_id") is not None \
                and str(o.get("course_section_id")) in sections:
            out.append({**o, "how": "section"})
    return out


def effective(assignment: dict, overrides: list[dict], user_id, section_ids) -> dict:
    """The due and lock dates this student is actually held to, and from where.

    Canvas's own rule when more than one override reaches a student is that the
    most lenient wins, so that is the rule here: the latest due date, and an
    override with no due date at all beats every dated one.
    """
    mine = applicable(overrides, user_id, section_ids)
    base = {
        "due_at": assignment.get("due_at"),
        "lock_at": assignment.get("lock_at"),
        "source": "the class due date",
        "override_id": None,
        "override_title": "",
        "shared_with": 0,
        "how": "everyone",
    }
    if not mine:
        return base

    def rank(o):
        # No due date is the most lenient thing an override can say.
        when = parse_iso(o.get("due_at"))
        return (1, datetime.max.replace(tzinfo=timezone.utc)) if when is None else (0, when)

    best = max(mine, key=rank)
    ids = [str(u) for u in (best.get("student_ids") or [])]
    adhoc = best.get("how") == "adhoc"
    return {
        "due_at": best.get("due_at"),
        "lock_at": best.get("lock_at") if best.get("lock_at") is not None
                   else assignment.get("lock_at"),
        "source": ("an extension already on this assignment" if adhoc and is_ours(best.get("title"))
                   else "a date already set just for them" if adhoc
                   else "their section's date"),
        "override_id": best.get("id") if adhoc else None,
        "override_title": best.get("title") or "",
        # How many OTHER students ride on that same override. Anything above
        # zero is why a row gets left alone rather than rewritten.
        "shared_with": max(0, len(ids) - 1) if adhoc else 0,
        "how": best.get("how"),
    }


# ------------------------------------------------------------------ the plan
def plan(students: list[dict], targets: list[dict], days: int,
         submitted: set | None = None, include_submitted: bool = False) -> dict:
    """Every date change this would make, without making any of them.

    `students` are {user_id, name}; `targets` are assignments already narrowed
    to the window, each carrying its course, its overrides, the timezone to do
    the arithmetic in, and `enrolled` / `sections` for the course it is in.
    `submitted` holds (assignment_id, user_id) pairs already turned in.
    """
    days = int(days)
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"{days} days is outside what this tool will move a "
                         f"deadline (1 to {MAX_DAYS}).")
    submitted = submitted or set()
    rows: list[dict] = []
    skipped: list[dict] = []

    for target in targets:
        tz_name = target.get("time_zone")
        overrides = target.get("overrides") or []
        enrolled = target.get("enrolled")
        sections = target.get("sections") or {}
        for student in students:
            uid = str(student.get("user_id"))
            base = {
                "course_id": str(target.get("course_id")),
                "course_label": target.get("course_label") or "",
                "assignment_id": str(target.get("assignment_id")),
                "title": target.get("title") or "",
                "kind": target.get("kind") or "assignment",
                "html_url": target.get("html_url") or "",
                "user_id": uid,
                "name": student.get("name") or uid,
            }
            if enrolled is not None and uid not in enrolled:
                skipped.append({**base, "why": "not in this course", "quiet": True})
                continue

            now = effective(target, overrides, uid, sections.get(uid))
            if not now["due_at"]:
                skipped.append({**base, "why": "no due date applies to them here,"
                                               " so there is nothing to extend"})
                continue
            if now["shared_with"]:
                skipped.append({
                    **base, "why": f"their date here comes from an override shared "
                                   f"with {now['shared_with']} other student(s), "
                                   f"\"{now['override_title']}\". Moving it would "
                                   f"move theirs too, so it is left alone.",
                    "from_due": now["due_at"], "blocked": True})
                continue

            already = (base["assignment_id"], uid) in submitted
            if already and not include_submitted:
                skipped.append({**base, "why": "already turned in",
                                "from_due": now["due_at"], "submitted": True})
                continue

            to_due = shift(now["due_at"], days, tz_name)
            to_lock = shift(now["lock_at"], days, tz_name) if now["lock_at"] else None
            # A lock that would still land before the new deadline makes the
            # extension a lie: Canvas stops accepting the work first.
            if to_lock and parse_iso(to_lock) < parse_iso(to_due):
                to_lock = to_due
            rows.append({
                **base,
                "from_due": now["due_at"], "to_due": to_due,
                "from_lock": now["lock_at"], "to_lock": to_lock,
                "source": now["source"],
                "override_id": now["override_id"],
                "action": "update" if now["override_id"] else "create",
                "submitted": already,
                "time_zone": tz_name,
            })

    if len(rows) > MAX_ROWS:
        raise ValueError(
            f"That would move {len(rows)} dates, past the {MAX_ROWS} this tool "
            f"will do at once. Narrow the window or the courses.")

    rows.sort(key=lambda r: (r["course_label"], parse_iso(r["from_due"]) or datetime.max
                             .replace(tzinfo=timezone.utc), r["name"]))
    skipped.sort(key=lambda r: (r["course_label"], r["title"], r["name"]))
    return {"rows": rows, "skipped": skipped, "days": days}


def describe(result: dict, days: int | None = None) -> str:
    """The one line somebody has to agree to before anything is written."""
    rows = result.get("rows") or []
    days = int(days if days is not None else result.get("days") or 0)
    if not rows:
        return "Nothing to move -- no dates in that window need changing."
    people = {r["user_id"] for r in rows}
    courses = {r["course_id"] for r in rows}
    names = sorted({r["name"] for r in rows})
    who = (names[0] if len(names) == 1
           else f"{len(people)} students" if len(names) > 3
           else ", ".join(names[:-1]) + " and " + names[-1])
    return (f"Give {who} {days} more day{'' if days == 1 else 's'} on "
            f"{len(rows)} due date{'' if len(rows) == 1 else 's'} across "
            f"{len(courses)} course{'' if len(courses) == 1 else 's'}. "
            f"Nobody else's dates change.")


def batches(rows: list[dict]) -> list[dict]:
    """One Canvas write per row: an override belongs to one student.

    Grouped only for the confirmation fingerprint and the record, so a plan
    that has drifted since it was shown cannot be applied against the old
    agreement.
    """
    return [{"course_id": r["course_id"], "assignment_id": r["assignment_id"],
             "user_id": r["user_id"], "to_due": r["to_due"], "to_lock": r["to_lock"],
             "action": r["action"], "override_id": r["override_id"]}
            for r in rows]
