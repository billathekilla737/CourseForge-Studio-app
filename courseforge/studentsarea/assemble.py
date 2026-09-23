"""Build one student's dossier from Canvas and what this machine already has.

The standing note and a short history of what the Studio has already recorded
live in the instructor's Canvas user files (`students/<id>.json`), so another
computer signed in as the same person can show the same page.
"""
from __future__ import annotations

import json
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from .. import audit, identity, inbox, nicknames, students as tagged, terms
from ..routing import HTTPError

HISTORY_CAP = 200


def person(app, user_id: str) -> dict:
    """The row taught_students already assembled: name, SIS, courses."""
    uid = str(user_id)
    listing = app.taught_students(False)
    for row in listing.get("students") or []:
        if str(row.get("user_id")) == uid:
            return row
    raise HTTPError(404, "That person is not in any course this token teaches.")


def standing(app, user_id: str) -> dict | None:
    uid = str(user_id)
    for student in app.roster.load():
        if str(student.user_id) == uid:
            return student.to_json()
    return None


def _term_summary(app) -> dict:
    try:
        courses = [c for c in app.courses(False) if not c.get("excluded")]
    except Exception:  # noqa: BLE001
        return {"terms": [], "default": ""}
    return terms.summarize(courses)


def _row_in_term(row: dict, term: str) -> bool:
    if not term:
        return True
    refs = row.get("course_refs") or []
    if not refs:
        return True
    if not any("term" in r for r in refs):
        return True
    return any((r.get("term") or "") == term for r in refs)


def _name_key(row: dict) -> str:
    """Last name, then the full name, so the list reads like a roster."""
    sort = str(row.get("sortable_name") or "").strip()
    if sort:
        return sort.lower()
    parts = str(row.get("name") or "").split()
    last = parts[-1] if parts else ""
    return f"{last} {row.get('name') or ''}".lower()


def search(app, query: str, term: str | None = None, limit: int = 500) -> dict:
    """The master list for one term, optionally narrowed by a name or id.

    An empty query is the whole list. The default term is the one the calendar
    says we are in, not every course this login has ever taught.
    """
    summary = _term_summary(app)
    if term is None:
        term = summary.get("default") or ""
    if term == "__all":
        term = ""
    q = " ".join(str(query or "").lower().split())
    try:
        listing = app.taught_students(False, term=term or None)
    except TypeError:
        listing = app.taught_students(False)
    hits = []
    for row in listing.get("students") or []:
        if not _row_in_term(row, term):
            continue
        nick = nicknames.lookup(row.get("user_id"), root=getattr(app.store, "root", None))
        legal = row.get("name") or ""
        if q:
            hay = " ".join([
                str(legal),
                str(row.get("sortable_name") or ""),
                nick,
                nicknames.format_name(legal, nick),
                str(row.get("sis_user_id") or ""),
                str(row.get("login_id") or ""),
            ]).lower()
            if q not in hay:
                continue
        hits.append({
            "user_id": row.get("user_id"),
            "name": legal,
            "nickname": nick,
            "display_name": nicknames.format_name(legal, nick),
            "sortable_name": row.get("sortable_name") or "",
            "sis_user_id": row.get("sis_user_id") or "",
            "login_id": row.get("login_id") or "",
            "courses": row.get("courses") or [],
            "course_refs": row.get("course_refs") or [],
            "on_roster": bool(row.get("on_roster")),
        })
        if len(hits) >= limit:
            break
    hits.sort(key=_name_key)
    return {
        "query": q,
        "term": term,
        "terms": summary.get("terms") or [],
        "students": hits,
        "count": len(hits),
        "total": listing.get("count") or 0,
        "note": "This term's students. Names stay on this screen.",
    }


def _key(user_id: str) -> str:
    return f"students/{user_id}.json"


def _sync_payload(app, user_id: str) -> dict:
    uid = str(user_id)
    key = _key(uid)
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "get"):
        return {"user_id": uid, "notes": "", "history": []}
    env = sync.get(key)
    payload = (env or {}).get("payload") if isinstance(env, dict) else None
    if not isinstance(payload, dict):
        payload = {}
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    return {
        "user_id": uid,
        "notes": str(payload.get("notes") or ""),
        "history": [h for h in history if isinstance(h, dict)][:HISTORY_CAP],
        "rev": (env or {}).get("rev") if isinstance(env, dict) else None,
        "written_at": (env or {}).get("written_at") if isinstance(env, dict) else None,
        "synced": sync is not None,
    }


def notes(app, user_id: str) -> dict:
    saved = _sync_payload(app, user_id)
    return {
        "notes": saved.get("notes") or "",
        "key": _key(str(user_id)),
        "synced": bool(saved.get("synced")),
        "rev": saved.get("rev"),
        "written_at": saved.get("written_at"),
        "history": saved.get("history") or [],
    }


def _event_key(item: dict) -> str:
    return "|".join([
        str(item.get("at") or ""),
        str(item.get("kind") or ""),
        str(item.get("course_id") or ""),
        str(item.get("assignment_id") or ""),
        str(item.get("summary") or "")[:120],
    ])


def _clip(text, n=240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def local_history(app, user_id: str) -> list[dict]:
    """Grades, submissions and record lines already on this computer."""
    uid = str(user_id)
    root = Path(app.store.root)
    courses = {str(c.get("id")): c for c in (app.store.courses() or []) if c.get("id")}
    events: list[dict] = []
    if not root.is_dir():
        return events
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
    account = root / "audit"
    if account.is_dir():
        dirs.append(root)
    for folder in dirs:
        cid = folder.name if folder.name.isdigit() else ""
        course = courses.get(cid) or {}
        label = course.get("course_code") or course.get("name") or cid or "Account"
        if cid:
            for draft_path in folder.glob("*/draft.json"):
                aid = draft_path.parent.name
                draft = app.store.read(draft_path, {}) or {}
                entry = (draft.get("students") or {}).get(uid)
                if not isinstance(entry, dict):
                    continue
                possible = draft.get("points_possible")
                score = entry.get("total")
                bits = []
                if score is not None:
                    bits.append(f"score {score}" + (f"/{possible}" if possible else ""))
                if entry.get("needs_human"):
                    bits.append("needs review")
                if entry.get("source"):
                    bits.append(str(entry.get("source")))
                events.append({
                    "at": entry.get("graded_at") or entry.get("edited_at")
                         or draft.get("last_graded_at") or "",
                    "kind": "grade",
                    "course_id": cid,
                    "course": label,
                    "assignment_id": aid,
                    "title": (app.store.read(draft_path.parent / "assignment.json", {}) or {}).get("name")
                             or aid,
                    "summary": _clip(", ".join(bits) or "graded on this computer"),
                })
            for extracted_path in folder.glob("*/extracted.json"):
                aid = extracted_path.parent.name
                try:
                    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                info = (extracted or {}).get(uid) if isinstance(extracted, dict) else None
                if not isinstance(info, dict) or not info.get("submitted_at"):
                    continue
                late = "late" if info.get("late") else "on time"
                events.append({
                    "at": info.get("submitted_at") or "",
                    "kind": "submission",
                    "course_id": cid,
                    "course": label,
                    "assignment_id": aid,
                    "title": (app.store.read(extracted_path.parent / "assignment.json", {}) or {}).get("name")
                             or aid,
                    "summary": _clip(f"{info.get('status') or 'submitted'}, {late}"),
                })
        for row in audit.read(folder, limit=80, student=uid):
            events.append({
                "at": row.get("at") or "",
                "kind": "record",
                "course_id": cid,
                "course": label,
                "assignment_id": str((row.get("detail") or {}).get("assignment_id") or ""),
                "title": row.get("area") or "record",
                "summary": _clip(row.get("sentence") or row.get("action") or ""),
            })
    return events


def merge_history(saved: list[dict], local: list[dict]) -> list[dict]:
    """Union by content, newest first, capped."""
    by_key: dict[str, dict] = {}
    for item in list(saved or []) + list(local or []):
        if not isinstance(item, dict):
            continue
        by_key[_event_key(item)] = item
    rows = list(by_key.values())
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:HISTORY_CAP]


def remember(app, user_id: str, notes_text: str, history: list[dict]) -> dict:
    """Write notes and history to Canvas user files. Never raises on conflict."""
    uid = str(user_id)
    sync = getattr(app, "state_sync", None)
    payload = {"user_id": uid, "notes": notes_text or "", "history": history[:HISTORY_CAP]}
    if sync is None or not hasattr(sync, "put"):
        return {"synced": False, "payload": payload}
    try:
        out = sync.put(_key(uid), payload)
    except Exception:  # noqa: BLE001  a conflict or a down Canvas must not blank the page
        return {"synced": False, "payload": payload, "conflict": True}
    return {"synced": True, "result": out, "payload": payload}


def inbox_with(app, user_id: str) -> list[dict]:
    uid = str(user_id)
    try:
        listing = inbox.listing(app, limit=40)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    hits = []
    for thread in listing.get("threads") or []:
        people = thread.get("with") or []
        if any(str(p.get("user_id")) == uid for p in people):
            hits.append(thread)
    return hits


def grades_here(app, user_id: str, course_id) -> dict | None:
    if not course_id:
        return None
    found = identity.known(app, user_id)
    if not found:
        return None
    tag, _row = found
    try:
        return tagged.student_view(app, course_id, tag)
    except Exception:  # noqa: BLE001
        return None


def dossier(app, user_id: str, course_id=None) -> dict:
    """Identity, courses, accommodation, local work, record, notes, inbox.

    Live quiz extras and deadline overrides stay a second request: they walk
    every course in Canvas. The history written here is copied to Canvas user
    files so the next computer can show it without this machine's drafts.
    """
    uid = str(user_id)
    row = person(app, uid)
    legal = row.get("name") or ""
    nick = nicknames.lookup(uid, root=getattr(app.store, "root", None))
    saved = _sync_payload(app, uid)
    history = merge_history(saved.get("history") or [], local_history(app, uid))
    if history != (saved.get("history") or []):
        remembered = remember(app, uid, saved.get("notes") or "", history)
        if remembered.get("conflict"):
            saved["conflict"] = True
    return {
        "user_id": uid,
        "name": legal,
        "legal_name": legal,
        "nickname": nick,
        "display_name": nicknames.format_name(legal, nick),
        "sis_user_id": row.get("sis_user_id") or "",
        "login_id": row.get("login_id") or "",
        "courses": row.get("courses") or [],
        "course_refs": row.get("course_refs") or [],
        "on_roster": bool(row.get("on_roster")),
        "standing": standing(app, uid),
        "notes": {
            "notes": saved.get("notes") or "",
            "key": _key(uid),
            "synced": bool(saved.get("synced")),
            "rev": saved.get("rev"),
            "written_at": saved.get("written_at"),
            "conflict": bool(saved.get("conflict")),
        },
        "history": history,
        "inbox": inbox_with(app, uid),
        "grades": grades_here(app, uid, course_id),
        "course_id": str(course_id) if course_id else None,
        "note": "Names stay on this screen. The note and the history copy to your Canvas files.",
    }


def _courses_for_live(app, row: dict) -> tuple[list[dict], str]:
    """This term only, and only courses this student is actually in."""
    summary = _term_summary(app)
    term = summary.get("default") or ""
    try:
        courses = [c for c in app.courses(False) if not c.get("excluded")]
    except Exception:  # noqa: BLE001
        courses = []
    if term:
        courses = [c for c in courses if (c.get("term_label") or "") == term]
    enrolled = {str(r.get("id")) for r in (row.get("course_refs") or []) if r.get("id")}
    if enrolled:
        courses = [c for c in courses if str(c.get("id")) in enrolled]
    return courses, term


def _overrides_for(app, course_id: str) -> list[dict]:
    """Assignment rows that already carry their overrides, when the client can."""
    client = app.client
    bundled = getattr(client, "assignments_with_overrides", None)
    if callable(bundled):
        try:
            return list(bundled(course_id) or [])
        except Exception:  # noqa: BLE001
            return []
    try:
        return list(client.assignments(course_id) or [])
    except Exception:  # noqa: BLE001
        return []


def _quiz_extra(app, uid: str, course_id: str, label: str, quiz: dict) -> dict | None:
    qid = str(quiz.get("id") or "")
    if not qid or not hasattr(app, "_granted"):
        return None
    try:
        info = (app._granted(course_id, qid) or {}).get(uid)
    except Exception:  # noqa: BLE001
        return None
    if not info or not any(info.get(k) for k in ("extra_time", "extra_attempts", "manually_unlocked")):
        return None
    return {
        "course_id": course_id, "course": label,
        "quiz_id": qid, "title": quiz.get("title") or qid,
        **info,
    }


def live(app, user_id: str, log=lambda *_a, **_k: None) -> dict:
    """Quiz extras and deadline overrides for this person, this term only.

    Older courses are not read. Overrides come back with the assignment list,
    so a course is a handful of Canvas calls instead of one per assignment.
    """
    uid = str(user_id)
    row = person(app, uid)
    courses, term = _courses_for_live(app, row)
    extras, overrides = [], []
    total = max(len(courses), 1)
    if not courses:
        log(f"no courses this term ({term or 'current'})", 1, 1)
        return {"user_id": uid, "term": term, "courses_checked": 0,
                "quiz_extras": [], "extensions": []}
    log(f"this term only · {len(courses)} course(s)", 0, total)
    quiz_jobs = []
    for index, course in enumerate(courses, start=1):
        cid = str(course.get("id"))
        label = (getattr(app, "_cached_course_label", lambda _c: None)(cid)
                 or course.get("name") or cid)
        log(f"{index}/{len(courses)} · {label[:40]}", index, total)
        for assignment in _overrides_for(app, cid):
            aid = str(assignment.get("id") or "")
            bundled = "overrides" in assignment
            rows = assignment.get("overrides") if bundled else None
            if rows is None:
                try:
                    rows = app.client.assignment_overrides(cid, aid)
                except Exception:  # noqa: BLE001
                    rows = []
            for ov in rows or []:
                ids = [str(x) for x in (ov.get("student_ids") or [])]
                title = ov.get("title") or ""
                if uid not in ids:
                    continue
                if ids and (title.startswith("Extension") or len(ids) == 1):
                    overrides.append({
                        "course_id": cid, "course": label,
                        "assignment_id": aid,
                        "title": assignment.get("name") or aid,
                        "override_title": title,
                        "due_at": ov.get("due_at"),
                        "lock_at": ov.get("lock_at"),
                    })
        try:
            quizzes = app.client.quizzes(cid) if hasattr(app.client, "quizzes") else []
        except Exception:  # noqa: BLE001
            quizzes = []
        for quiz in quizzes or []:
            if quiz.get("id"):
                quiz_jobs.append((cid, label, quiz))
    if quiz_jobs:
        workers = min(6, len(quiz_jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            found = pool.map(
                lambda job: _quiz_extra(app, uid, job[0], job[1], job[2]),
                quiz_jobs)
        extras = [row for row in found if row]
    return {"user_id": uid, "term": term, "courses_checked": len(courses),
            "quiz_extras": extras, "extensions": overrides}
