"""Who needs a look: a plain risk score for students you teach this term.

REMOVAL: this whole file. Also remove the routes in studentsarea/routes.py
marked RISK-INDEX, the Students-page block in web/js/student.js marked
RISK-INDEX, the .stRisk rules in web/css/student.css, and
docs/RISK-INDEX.md. Delete data/risk-index.json and the Canvas user file
courseforge-studio/state/risk-index.json. Nothing here is sent to Claude.

The assignment list is read every time, so a new due date is noticed.
Submissions are read only for assignments that are due and were not in the
last scan. scanned_at is that cursor. It is saved locally and copied to
Canvas user files, and loaded from there before a scan uses it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

FILE_NAME = "risk-index.json"
SYNC_KEY = "risk-index.json"
HISTORY_CAP = 8


def band(score: int) -> str:
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def score_attendance(absent: int, tardy: int) -> tuple[int, list[str]]:
    """A separate 0 to 100 for showing up. Absences carry it. Tardies add a little.

    One absence is 12, up to 72. One tardy is 2, up to 16. An excused mark
    and a present mark add nothing. The coursework score is not changed.
    """
    try:
        absent = max(0, int(absent or 0))
    except (TypeError, ValueError):
        absent = 0
    try:
        tardy = max(0, int(tardy or 0))
    except (TypeError, ValueError):
        tardy = 0
    points = min(72, absent * 12) + min(16, tardy * 2)
    reasons: list[str] = []
    if absent:
        reasons.append(f"{absent} absence" + ("" if absent == 1 else "s"))
    if tardy:
        reasons.append(f"{tardy} tard" + ("y" if tardy == 1 else "ies"))
    return min(100, points), reasons


def attendance_counts(app, course_id) -> dict[str, dict]:
    """Absences and tardies already marked for this course. No Canvas call.

    Only class days count. Excused and present do not. A course with no
    calendar yet contributes nothing.
    """
    try:
        from ..attendance import book as attendance
        stored = attendance.load(app, course_id)
    except Exception:  # noqa: BLE001
        return {}
    if not (stored.get("weekdays") or stored.get("marks") or stored.get("meet")):
        return {}
    break_days = set()
    try:
        for iso in attendance.breaks_between(app, course_id, stored):
            day = attendance.parse_day(iso)
            if day:
                break_days.add(day)
    except Exception:  # noqa: BLE001
        pass
    try:
        meetings = {d.isoformat() for d in attendance.meeting_dates(stored, break_days)}
    except Exception:  # noqa: BLE001
        meetings = set()
    marks = attendance.public_marks(stored)
    days = meetings or set(marks)
    out: dict[str, dict] = {}
    for day in days:
        for uid, mark in (marks.get(day) or {}).items():
            status = (mark or {}).get("status")
            if status not in ("absent", "tardy"):
                continue
            row = out.setdefault(str(uid), {"absent": 0, "tardy": 0})
            row[status] += 1
    return out


def score_course(stats: dict) -> tuple[int, list[str]]:
    """0 to 100. Higher means this class needs a look sooner.

    Grade under 60, missing past-due work, zeros, and a long quiet stretch
    each add points. A class with nothing due yet stays at 0.
    """
    points = 0
    reasons: list[str] = []
    grade = stats.get("grade")
    if isinstance(grade, (int, float)):
        if grade < 60:
            points += 35
            reasons.append(f"grade {grade:.0f}%")
        elif grade < 70:
            points += 20
            reasons.append(f"grade {grade:.0f}%")
        elif grade < 80:
            points += 8
            reasons.append(f"grade {grade:.0f}%")
    missing = int(stats.get("missing") or 0)
    if missing:
        points += min(40, missing * 10)
        reasons.append(
            f"{missing} past-due item" + ("" if missing == 1 else "s") + " not turned in")
    zeros = int(stats.get("zeros") or 0)
    if zeros:
        points += min(20, zeros * 5)
        reasons.append(f"{zeros} zero" + ("" if zeros == 1 else "s"))
    days = stats.get("days_since_submit")
    if missing and isinstance(days, int) and days >= 14:
        points += 12
        reasons.append(f"no submission in {days} days")
    quiet = stats.get("days_since_activity")
    if isinstance(quiet, int) and quiet >= 14:
        points += 8
        reasons.append(f"no Canvas activity in {quiet} days")
    return min(100, points), reasons


def _when(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        got = datetime.fromisoformat(text)
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    return got


def _days_since(moment, now) -> int | None:
    if moment is None:
        return None
    return max(0, (now - moment).days)


def due_unscanned(assignments, scanned_ids, now) -> list[dict]:
    """Assignments that are due now and were not part of an earlier check."""
    seen = {str(x) for x in (scanned_ids or [])}
    out = []
    for assignment in assignments or []:
        if not isinstance(assignment, dict) or assignment.get("published") is False:
            continue
        due = _when(assignment.get("due_at"))
        if due is None or due > now:
            continue
        aid = str(assignment.get("id") or "")
        if aid and aid not in seen:
            out.append(assignment)
    return out


def fold_stats(kept: dict | None, new_assignments, submissions, enrollment, now) -> dict:
    """Add newly due items onto counts from the last scan. Old items stay put."""
    kept = kept or {}
    missing = {str(x) for x in (kept.get("missing_ids") or [])}
    zeros = {str(x) for x in (kept.get("zero_ids") or [])}
    last_submit = _when(kept.get("last_submit"))
    by_assignment = {}
    for sub in submissions or []:
        aid = str(sub.get("assignment_id") or "")
        if aid:
            by_assignment[aid] = sub
        submitted = _when(sub.get("submitted_at"))
        if submitted and (last_submit is None or submitted > last_submit):
            last_submit = submitted
    for assignment in new_assignments or []:
        aid = str(assignment.get("id") or "")
        if not aid:
            continue
        sub = by_assignment.get(aid) or {}
        if sub.get("excused"):
            continue
        turned_in = bool(sub.get("submitted_at")) or sub.get("workflow_state") in (
            "submitted", "graded", "pending_review")
        submitted = _when(sub.get("submitted_at"))
        if submitted and (last_submit is None or submitted > last_submit):
            last_submit = submitted
        if sub.get("missing") or not turned_in:
            missing.add(aid)
            zeros.discard(aid)
            continue
        missing.discard(aid)
        try:
            possible = float(assignment.get("points_possible") or 0)
            score = sub.get("score")
            if possible > 0 and score is not None and float(score) == 0:
                zeros.add(aid)
            else:
                zeros.discard(aid)
        except (TypeError, ValueError):
            pass
    grade = kept.get("grade")
    activity = _when(kept.get("last_activity_at"))
    name = kept.get("name") or ""
    if enrollment:
        grades = enrollment.get("grades") or {}
        try:
            fresh = grades.get("current_score")
            fresh = float(fresh) if fresh is not None else None
        except (TypeError, ValueError):
            fresh = None
        if fresh is not None and fresh <= 100:
            grade = fresh
        activity = _when(enrollment.get("last_activity_at")) or activity
        user = enrollment.get("user") or {}
        name = user.get("name") or user.get("sortable_name") or name
    return {
        "name": name,
        "grade": grade,
        "missing": len(missing),
        "zeros": len(zeros),
        "missing_ids": sorted(missing),
        "zero_ids": sorted(zeros),
        "last_submit": last_submit.isoformat(timespec="seconds") if last_submit else None,
        "last_activity_at": activity.isoformat(timespec="seconds") if activity else None,
        "days_since_submit": _days_since(last_submit, now),
        "days_since_activity": _days_since(activity, now),
    }


def _flatten_submissions(items) -> list[dict]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        nested = item.get("submissions")
        if isinstance(nested, list) and "assignment_id" not in item:
            out.extend(row for row in nested if isinstance(row, dict))
        elif item.get("assignment_id"):
            out.append(item)
    return out


def _path(app) -> Path:
    return Path(app.store.root) / FILE_NAME


def load(app) -> dict:
    data = app.store.read(_path(app), {}) or {}
    return data if isinstance(data, dict) else {}


def load_for_use(app) -> dict:
    """Local scan, replaced by the Canvas copy when that one was scanned later.

    The date is the cursor for the next check, so it has to be on this machine
    before a scan decides what is already done.
    """
    local = load(app)
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "get"):
        return local
    try:
        env = sync.get(SYNC_KEY)
    except Exception:  # noqa: BLE001
        return local
    remote = (env or {}).get("payload") if isinstance(env, dict) else None
    if not isinstance(remote, dict) or not remote.get("scanned_at"):
        return local
    if not local.get("scanned_at") or str(remote.get("scanned_at")) > str(local.get("scanned_at")):
        try:
            app.store.write(_path(app), remote)
        except Exception:  # noqa: BLE001
            return remote
        return remote
    return local


def _history_entry(previous: dict) -> dict | None:
    if not previous.get("scanned_at"):
        return None
    return {
        "at": previous.get("scanned_at"),
        "overall": previous.get("overall_scores") or {},
        "courses": previous.get("course_scores") or {},
        "attend": previous.get("attend_scores") or {},
        "attend_courses": previous.get("attend_course_scores") or {},
    }


def scan(app, log=lambda *_a, **_k: None) -> dict:
    """Read this term, one course at a time, and save data/risk-index.json."""
    from . import assemble

    started = time.monotonic()
    now = datetime.now(timezone.utc)
    summary = assemble._term_summary(app)
    term = summary.get("default") or ""
    try:
        courses = [c for c in app.courses(False) if not c.get("excluded")]
    except Exception:  # noqa: BLE001
        courses = []
    if term:
        courses = [c for c in courses if (c.get("term_label") or "") == term]
    previous = load_for_use(app)
    last_scanned = previous.get("scanned_at") or ""
    ledger = previous.get("ledger") if isinstance(previous.get("ledger"), dict) else {}
    history = list(previous.get("history") or [])
    remembered = _history_entry(previous)
    if remembered:
        history.append(remembered)
    history = history[-HISTORY_CAP:]
    prev_overall = (history[-1].get("overall") if history else {}) or {}
    prev_courses = (history[-1].get("courses") if history else {}) or {}
    prev_attend = (history[-1].get("attend") if history else {}) or {}
    prev_attend_courses = (history[-1].get("attend_courses") if history else {}) or {}

    people: dict[str, dict] = {}
    course_meta = []
    failures = []
    new_ledger: dict[str, dict] = {}
    total = len(courses)
    if not total:
        log("No classes this term. Nothing to check.", 1, 1)
    else:
        log(f"Reading class 1 of {total}", 0, total)
    for index, course in enumerate(courses, start=1):
        cid = str(course.get("id") or "")
        if not cid:
            continue
        label = course.get("course_code") or course.get("name") or cid
        # done stays at the classes already finished, so the last class does
        # not look complete while it is still being read.
        log(f"Reading class {index} of {total}: {str(label)[:40]}", index - 1, total)
        try:
            built, new_ids, skipped = _read_course(
                app.client, cid, now, (ledger.get(cid) or {}))
        except Exception as exc:  # noqa: BLE001
            failures.append({"course_id": cid, "name": label,
                             "error": f"{type(exc).__name__}: {exc}"})
            log(f"Could not read {str(label)[:40]}", index, total)
            continue
        if new_ids:
            log(f"{str(label)[:32]}: {len(new_ids)} newly due, {skipped} already scanned",
                index, total)
        else:
            log(f"{str(label)[:32]}: nothing newly due"
                + (f" since {last_scanned[:10]}" if last_scanned else ""),
                index, total)
        course_meta.append({
            "id": cid, "name": label, "students": len(built),
            "new_due": len(new_ids), "already_scanned": skipped,
        })
        attend_map = attendance_counts(app, cid)
        new_ledger[cid] = {
            "assignments": sorted(set((ledger.get(cid) or {}).get("assignments") or []) | set(new_ids)),
            "students": {
                uid: {k: stats.get(k) for k in (
                    "name", "grade", "missing_ids", "zero_ids",
                    "last_submit", "last_activity_at")}
                for uid, stats in built.items()
            },
        }
        for uid, stats in built.items():
            score, reasons = score_course(stats)
            marks = attend_map.get(str(uid)) or {}
            attend_score, attend_reasons = score_attendance(
                marks.get("absent") or 0, marks.get("tardy") or 0)
            person = people.setdefault(uid, {
                "user_id": uid,
                "name": stats.get("name") or "",
                "courses": [],
            })
            if stats.get("name") and not person.get("name"):
                person["name"] = stats["name"]
            course_trend = None
            before = (prev_courses.get(uid) or {}).get(cid)
            if before is not None:
                course_trend = score - int(before)
            attend_trend = None
            attend_before = (prev_attend_courses.get(uid) or {}).get(cid)
            if attend_before is not None:
                attend_trend = attend_score - int(attend_before)
            person["courses"].append({
                "id": cid,
                "name": label,
                "score": score,
                "band": band(score),
                "trend": course_trend,
                "reasons": [f"{label}: {reason}" for reason in reasons],
                "attend": attend_score,
                "attend_band": band(attend_score),
                "attend_trend": attend_trend,
                "attend_reasons": [f"{label}: {reason}" for reason in attend_reasons],
                "absent": int(marks.get("absent") or 0),
                "tardy": int(marks.get("tardy") or 0),
                "grade": stats.get("grade"),
                "missing": stats.get("missing") or 0,
                "zeros": stats.get("zeros") or 0,
            })
    students = []
    overall_scores: dict[str, int] = {}
    course_scores: dict[str, dict[str, int]] = {}
    attend_scores: dict[str, int] = {}
    attend_course_scores: dict[str, dict[str, int]] = {}
    for uid, person in people.items():
        rows = person["courses"]
        overall = max((row["score"] for row in rows), default=0)
        attend = max((row.get("attend") or 0 for row in rows), default=0)
        reasons = []
        attend_reasons = []
        for row in sorted(rows, key=lambda r: -r["score"]):
            reasons.extend(row["reasons"])
        for row in sorted(rows, key=lambda r: -(row.get("attend") or 0)):
            attend_reasons.extend(row.get("attend_reasons") or [])
        before = prev_overall.get(uid)
        trend = None if before is None else overall - int(before)
        attend_before = prev_attend.get(uid)
        attend_trend = None if attend_before is None else attend - int(attend_before)
        overall_scores[uid] = overall
        course_scores[uid] = {row["id"]: row["score"] for row in rows}
        attend_scores[uid] = attend
        attend_course_scores[uid] = {row["id"]: row.get("attend") or 0 for row in rows}
        students.append({
            "user_id": uid,
            "name": person.get("name") or "",
            "score": overall,
            "band": band(overall),
            "trend": trend,
            "reasons": reasons[:8],
            "attend": attend,
            "attend_band": band(attend),
            "attend_trend": attend_trend,
            "attend_reasons": attend_reasons[:6],
            "courses": rows,
        })
    students.sort(key=lambda s: (
        -max(s["score"], s.get("attend") or 0), (s.get("name") or "").lower()))
    counts = {"high": 0, "medium": 0, "low": 0}
    attend_counts = {"high": 0, "medium": 0, "low": 0}
    look = 0
    for row in students:
        counts[row["band"]] += 1
        attend_counts[row["attend_band"]] += 1
        if row["score"] >= 50 or (row.get("attend") or 0) >= 50:
            look += 1
    counts["look"] = look
    elapsed = round(time.monotonic() - started, 1)
    snapshot = {
        "scanned_at": now.isoformat(timespec="seconds"),
        "term": term,
        "seconds": elapsed,
        "courses_checked": len(course_meta),
        "course_list": course_meta,
        "failures": failures,
        "counts": counts,
        "attend_counts": attend_counts,
        "students": students,
        "overall_scores": overall_scores,
        "course_scores": course_scores,
        "attend_scores": attend_scores,
        "attend_course_scores": attend_course_scores,
        "history": history,
        "ledger": new_ledger,
        "new_due": sum(c.get("new_due") or 0 for c in course_meta),
        "already_scanned": sum(c.get("already_scanned") or 0 for c in course_meta),
        "note": "This term only. Work is grades and missing assignments. "
                "Attend is absences, with tardies counted lightly, and it is "
                "a separate score. A later check reads assignments that came "
                "due since the last scanned date, and leaves the rest as they "
                "were. Not sent to Claude.",
    }
    app.store.write(_path(app), snapshot)
    _backup(app, snapshot)
    log(f"Finished in {elapsed}s. {counts['high']} high, "
        f"{counts['medium']} medium, {counts['low']} low.",
        total or 1, total or 1)
    return {k: snapshot[k] for k in (
        "scanned_at", "term", "seconds", "courses_checked", "failures",
        "counts", "note")}


def _read_course(client, course_id: str, now, kept: dict) -> tuple[dict[str, dict], list[str], int]:
    """Return student stats, newly scanned assignment ids, and how many were skipped.

    The assignment list is always read, because that is how a new due date is
    discovered. Submissions and enrollments are read only for assignments that
    are due and were not in the last scan.
    """
    assignments = [a for a in client.paged(f"/courses/{course_id}/assignments", per_page=100)
                   if isinstance(a, dict)]
    scanned = [str(x) for x in (kept.get("assignments") or [])]
    fresh = due_unscanned(assignments, scanned, now)
    kept_students = kept.get("students") if isinstance(kept.get("students"), dict) else {}
    submissions: list[dict] = []
    enrollments: list[dict] = []
    if fresh:
        ids = [str(a.get("id")) for a in fresh if a.get("id")]
        submissions = _flatten_submissions(client.paged(
            f"/courses/{course_id}/students/submissions",
            student_ids=["all"], assignment_ids=ids, per_page=100))
        enrollments = [e for e in client.paged(
            f"/courses/{course_id}/enrollments",
            type=["StudentEnrollment"], state=["active"], per_page=100)
            if isinstance(e, dict)]
    by_user: dict[str, list] = {}
    for sub in submissions:
        uid = str(sub.get("user_id") or "")
        if uid:
            by_user.setdefault(uid, []).append(sub)
    enroll_by: dict[str, dict] = {}
    for enrollment in enrollments:
        if enrollment.get("type") not in (None, "", "StudentEnrollment"):
            continue
        uid = str(enrollment.get("user_id") or (enrollment.get("user") or {}).get("id") or "")
        if uid:
            enroll_by[uid] = enrollment
    uids = set(kept_students) | set(by_user) | set(enroll_by)
    found: dict[str, dict] = {}
    for uid in uids:
        found[uid] = fold_stats(
            kept_students.get(uid), fresh, by_user.get(uid) or [],
            enroll_by.get(uid), now)
    return found, [str(a.get("id")) for a in fresh if a.get("id")], len(scanned)


def _backup(app, snapshot: dict) -> None:
    """Copy the cursor and the scores to Canvas user files. Failures stay local."""
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        return
    payload = {
        "scanned_at": snapshot.get("scanned_at"),
        "term": snapshot.get("term"),
        "seconds": snapshot.get("seconds"),
        "courses_checked": snapshot.get("courses_checked"),
        "counts": snapshot.get("counts"),
        "attend_counts": snapshot.get("attend_counts"),
        "students": snapshot.get("students"),
        "overall_scores": snapshot.get("overall_scores"),
        "course_scores": snapshot.get("course_scores"),
        "attend_scores": snapshot.get("attend_scores"),
        "attend_course_scores": snapshot.get("attend_course_scores"),
        "history": snapshot.get("history"),
        "ledger": snapshot.get("ledger"),
        "new_due": snapshot.get("new_due"),
        "already_scanned": snapshot.get("already_scanned"),
        "note": snapshot.get("note"),
    }
    try:
        sync.put(SYNC_KEY, payload)
    except Exception:  # noqa: BLE001
        pass


def refresh_attendance(app, snapshot: dict) -> dict:
    """Put today's absences and tardies on a saved check. Does not call Canvas.

    The work score stays whatever the last check stored. Attendance is read
    from the calendars on this computer, so marking someone absent shows up
    here without another class-by-class read.
    """
    if not isinstance(snapshot, dict) or not snapshot.get("students"):
        return snapshot
    history = snapshot.get("history") or []
    prev = (history[-1].get("attend") if history else {}) or {}
    prev_courses = (history[-1].get("attend_courses") if history else {}) or {}
    cache: dict[str, dict] = {}
    students = []
    for person in snapshot.get("students") or []:
        if not isinstance(person, dict):
            continue
        person = dict(person)
        uid = str(person.get("user_id") or "")
        courses = []
        for row in person.get("courses") or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            cid = str(row.get("id") or "")
            if cid not in cache:
                cache[cid] = attendance_counts(app, cid)
            marks = cache[cid].get(uid) or {}
            attend_score, reasons = score_attendance(
                marks.get("absent") or 0, marks.get("tardy") or 0)
            before = (prev_courses.get(uid) or {}).get(cid)
            row["attend"] = attend_score
            row["attend_band"] = band(attend_score)
            row["attend_trend"] = None if before is None else attend_score - int(before)
            row["absent"] = int(marks.get("absent") or 0)
            row["tardy"] = int(marks.get("tardy") or 0)
            label = row.get("name") or cid
            row["attend_reasons"] = [f"{label}: {reason}" for reason in reasons]
            courses.append(row)
        attend = max((row.get("attend") or 0 for row in courses), default=0)
        before = prev.get(uid)
        person["courses"] = courses
        person["attend"] = attend
        person["attend_band"] = band(attend)
        person["attend_trend"] = None if before is None else attend - int(before)
        attend_reasons = []
        for row in sorted(courses, key=lambda r: -(r.get("attend") or 0)):
            attend_reasons.extend(row.get("attend_reasons") or [])
        person["attend_reasons"] = attend_reasons[:6]
        students.append(person)
    snapshot = dict(snapshot)
    snapshot["students"] = students
    attend_counts = {"high": 0, "medium": 0, "low": 0}
    look = 0
    for row in students:
        attend_counts[row["attend_band"]] += 1
        if (row.get("score") or 0) >= 50 or (row.get("attend") or 0) >= 50:
            look += 1
    counts = dict(snapshot.get("counts") or {})
    counts["look"] = look
    snapshot["counts"] = counts
    snapshot["attend_counts"] = attend_counts
    return snapshot


def public_view(snapshot: dict) -> dict:
    """What the page needs. History stays in the file."""
    if not snapshot or not snapshot.get("scanned_at"):
        return {"empty": True, "students": [], "note": "No check yet."}
    return {
        "empty": False,
        "scanned_at": snapshot.get("scanned_at"),
        "term": snapshot.get("term") or "",
        "seconds": snapshot.get("seconds"),
        "new_due": snapshot.get("new_due") or 0,
        "already_scanned": snapshot.get("already_scanned") or 0,
        "courses_checked": snapshot.get("courses_checked") or 0,
        "failures": snapshot.get("failures") or [],
        "counts": snapshot.get("counts") or {},
        "attend_counts": snapshot.get("attend_counts") or {},
        "students": snapshot.get("students") or [],
        "note": snapshot.get("note") or "",
    }
