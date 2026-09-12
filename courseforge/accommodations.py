"""Testing accommodations: who gets them, and what that means per test.

The problem this solves is arithmetic done by hand. A student approved for
time-and-a-half needs +23 minutes on a 45-minute test and +13 on a 25-minute
one, and Canvas takes extra MINUTES, never a multiplier. Working that out for
every student on every test in every course is the whole chore.

So the accommodation is stored the way the college grants it -- "1.5x time",
"+20 minutes", "one extra attempt" -- and the minutes are computed per quiz at
apply time.

Three things make the cross-course part work:

  * A Canvas user id is global to the instance, so the same person carries the
    same id in every course they are enrolled in. The roster is keyed on that,
    which is why one entry can reach every course without any name matching.
  * The roster lives in the data directory, not in a course, so it survives the
    term. Students repeat, and their accommodations do too.
  * Every plan is computed and shown before anything is sent, and rows that
    already hold the right value are marked as such rather than re-sent.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# What the college grants, and how it turns into minutes.
#   percent  -- a multiplier on each quiz's own limit (1.5x is percent=50)
#   minutes  -- a flat number of extra minutes, whatever the quiz is worth
KINDS = ("percent", "minutes")

# A ceiling on the roster. Not a technical limit -- a signal that something has
# gone wrong, because an accommodation list the length of a section is not an
# accommodation list.
MAX_ROSTER = 200


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Student:
    """One person's standing accommodation."""

    user_id: str
    name: str = ""
    sis_user_id: str = ""
    login_id: str = ""
    kind: str = "percent"           # one of KINDS
    percent: int = 50               # +50% == time and a half
    minutes: int = 0                # used when kind == "minutes"
    extra_attempts: int = 0
    manually_unlocked: bool = False
    note: str = ""
    updated: str = field(default_factory=_now)

    def extra_minutes_for(self, time_limit: int | None) -> int | None:
        """Extra minutes for a quiz with this time limit.

        None means "this does not apply here": a quiz with no time limit is
        already untimed, so extra time on it is meaningless and Canvas would
        record a number that never does anything.
        """
        if self.kind == "minutes":
            return int(self.minutes) if self.minutes else None
        if not time_limit or int(time_limit) <= 0:
            return None
        # Rounded UP on purpose. A student approved for 1.5x on a 45-minute
        # test is owed 67.5 minutes; giving 22 extra minutes instead of 23
        # quietly shorts them half a minute, and the accommodation is a floor,
        # not a target.
        return math.ceil(int(time_limit) * (int(self.percent) / 100.0))

    def label(self) -> str:
        if self.kind == "minutes":
            base = f"+{self.minutes} min"
        else:
            base = f"+{self.percent}% time"
            if self.percent in (50, 100):
                base += f" ({1 + self.percent / 100:g}x)"
        extras = []
        if self.extra_attempts:
            extras.append(f"+{self.extra_attempts} attempt"
                          + ("s" if self.extra_attempts != 1 else ""))
        if self.manually_unlocked:
            extras.append("can start while locked")
        return base + (", " + ", ".join(extras) if extras else "")

    def to_json(self) -> dict:
        return {
            "user_id": str(self.user_id), "name": self.name,
            "sis_user_id": self.sis_user_id, "login_id": self.login_id,
            "kind": self.kind, "percent": int(self.percent),
            "minutes": int(self.minutes),
            "extra_attempts": int(self.extra_attempts),
            "manually_unlocked": bool(self.manually_unlocked),
            "note": self.note, "updated": self.updated,
            "label": self.label(),
        }


def parse_student(raw: dict) -> Student:
    """Build a Student from a request body, refusing nonsense.

    Everything here arrives from a browser form, so each field is checked
    rather than trusted. Numbers are clamped instead of rejected where a clamp
    is obviously what was meant, and rejected where it is not.
    """
    uid = str(raw.get("user_id") or "").strip()
    if not uid.isdigit():
        raise ValueError(f"{uid or '(blank)'} is not a Canvas user id")

    kind = str(raw.get("kind") or "percent").strip()
    if kind not in KINDS:
        raise ValueError(f"unknown accommodation kind {kind!r}")

    percent = _as_int(raw.get("percent"), default=50)
    minutes = _as_int(raw.get("minutes"), default=0)
    attempts = _as_int(raw.get("extra_attempts"), default=0)

    if kind == "percent" and not 1 <= percent <= 400:
        raise ValueError(f"+{percent}% is not a time accommodation anyone grants "
                         "(1 to 400)")
    if kind == "minutes" and not 1 <= minutes <= 10080:
        raise ValueError(f"{minutes} extra minutes is outside what Canvas takes "
                         "(1 minute to one week)")
    if not 0 <= attempts <= 20:
        raise ValueError(f"{attempts} extra attempts looks like a typo (0 to 20)")

    return Student(
        user_id=uid,
        name=str(raw.get("name") or "").strip()[:120],
        sis_user_id=str(raw.get("sis_user_id") or "").strip()[:60],
        login_id=str(raw.get("login_id") or "").strip()[:120],
        kind=kind, percent=percent, minutes=minutes,
        extra_attempts=attempts,
        manually_unlocked=bool(raw.get("manually_unlocked")),
        note=str(raw.get("note") or "").strip()[:300],
        updated=_now(),
    )


def _as_int(value, default: int = 0) -> int:
    if value in (None, "", False):
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a number") from None


# --------------------------------------------------------------------- storage
class Roster:
    """The saved accommodation list, on disk next to the graded work."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[Student]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return []
        out: list[Student] = []
        for row in (raw.get("students") or []):
            try:
                out.append(parse_student(row))
            except ValueError:
                continue                # a row that no longer parses is dropped
        out.sort(key=lambda s: _sort_name(s.name) or s.user_id)
        return out

    def save(self, students: list[Student]) -> None:
        if len(students) > MAX_ROSTER:
            raise ValueError(
                f"{len(students)} students is past the {MAX_ROSTER} this list is "
                "meant to hold. If a whole section needs longer, change the "
                "quiz's own time limit instead.")
        # One id, one entry: the last one written wins rather than silently
        # applying two accommodations to the same person.
        by_id: dict[str, Student] = {}
        for s in students:
            by_id[str(s.user_id)] = s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"updated": _now(),
                "students": [s.to_json() for s in by_id.values()]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _sort_name(name: str) -> str:
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    return (parts[-1] + " " + " ".join(parts[:-1])).lower() if parts else ""


# ----------------------------------------------------------------- the plan
def plan(students: list[Student], quizzes: list[dict],
         existing: dict[tuple[str, str], dict] | None = None) -> dict:
    """Work out every per-student change, without sending anything.

    `quizzes` are dicts with course_id, course_label, quiz_id, title,
    time_limit and enrolled (the set of user ids actually in that course).
    `existing` maps (quiz_id, user_id) to whatever Canvas already holds, so a
    row that is already right is reported as such and not re-sent.

    Returns rows to apply, rows deliberately skipped, and per-quiz batches.
    """
    existing = existing or {}
    rows: list[dict] = []
    skipped: list[dict] = []

    for quiz in quizzes:
        qid = str(quiz.get("quiz_id"))
        enrolled = quiz.get("enrolled")
        limit = quiz.get("time_limit")
        for student in students:
            base = {
                "course_id": str(quiz.get("course_id")),
                "course_label": quiz.get("course_label") or "",
                "quiz_id": qid,
                "quiz_title": quiz.get("title") or "",
                "assignment_id": quiz.get("assignment_id"),
                "time_limit": limit,
                "user_id": str(student.user_id),
                "name": student.name or student.user_id,
            }
            # Not in that course: the commonest case by far when a roster is
            # applied term-wide, and not a problem worth reporting loudly.
            if enrolled is not None and str(student.user_id) not in enrolled:
                skipped.append({**base, "why": "not enrolled in this course",
                                "quiet": True})
                continue

            extra = student.extra_minutes_for(limit)
            want = {
                "extra_time": extra,
                "extra_attempts": student.extra_attempts or None,
                "manually_unlocked": True if student.manually_unlocked else None,
            }
            want = {k: v for k, v in want.items() if v is not None}
            if not want:
                skipped.append({**base, "why": "no time limit on this quiz, so "
                                               "extra time would do nothing"})
                continue

            have = existing.get((qid, str(student.user_id))) or {}
            same = all(_same(have.get(k), v) for k, v in want.items())
            if same:
                skipped.append({**base, **want, "why": "already set in Canvas",
                                "quiet": True, "already": True})
                continue

            rows.append({**base, **want,
                         "before": {k: have.get(k) for k in want},
                         "detail": student.label()})

    batches: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        batches.setdefault((row["course_id"], row["quiz_id"]), []).append(row)

    return {
        "rows": rows,
        "skipped": skipped,
        "batches": [
            {"course_id": cid, "quiz_id": qid,
             "course_label": items[0]["course_label"],
             "quiz_title": items[0]["quiz_title"],
             "time_limit": items[0]["time_limit"],
             "extensions": [
                 {k: r[k] for k in ("user_id", "extra_time", "extra_attempts",
                                    "manually_unlocked") if k in r}
                 for r in items],
             "names": [r["name"] for r in items]}
            for (cid, qid), items in batches.items()
        ],
    }


def _same(have, want) -> bool:
    """Whether Canvas already holds this value. Canvas returns 0 and null
    interchangeably for "nothing set", and bools as bools."""
    if isinstance(want, bool):
        return bool(have) == want
    return int(have or 0) == int(want or 0)


def describe(result: dict) -> str:
    """A one-line summary for the confirmation prompt."""
    rows = result.get("rows") or []
    if not rows:
        return "Nothing to change -- every accommodation is already set."
    quizzes = {(r["course_id"], r["quiz_id"]) for r in rows}
    people = {r["user_id"] for r in rows}
    courses = {r["course_id"] for r in rows}
    parts = [f"{len(rows)} accommodation change"
             + ("s" if len(rows) != 1 else ""),
             f"{len(people)} student" + ("s" if len(people) != 1 else ""),
             f"{len(quizzes)} quiz" + ("zes" if len(quizzes) != 1 else "")]
    if len(courses) > 1:
        parts.append(f"{len(courses)} courses")
    return ", ".join(parts)
