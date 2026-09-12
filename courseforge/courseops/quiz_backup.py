"""Back up a Classic Quiz inside its course as a new, unpublished quiz.

Ported from Backup-CanvasQuiz.ps1. Canvas refuses to duplicate quiz-backed
assignments (gotcha 5), so the copy is built by hand: a new quiz carrying the
same settings, then every question re-posted. The READ shape of an answer is
not the WRITE shape (gotcha 6); `question_read_to_write` does that mapping.

The copy is always created unpublished, whatever the source's state. The
result is verified through /questions (count and points), because an
unpublished quiz object reports stale totals on GET (gotcha 3).
"""
from __future__ import annotations

from collections import Counter

from .. import ledger
from ..canvas_content import ContentOps
from .common import (AREA, Gate, Log, Refused, append_history, area_dir, course_label,
                     load_json, now_iso, plural, quiet_log)

HISTORY = "quiz-backups.json"
TITLE_PREFIX = "BACKUP"
NOTE = "<p><strong>Unpublished backup</strong> of the original quiz, kept for reference.</p>"
COPIED_SETTINGS = ("quiz_type", "shuffle_answers", "allowed_attempts", "scoring_policy",
                   "one_question_at_a_time", "cant_go_back", "hide_results", "time_limit",
                   "access_code", "ip_filter", "require_lockdown_browser")


def backup_title(title: str | None, prefix: str = TITLE_PREFIX) -> str:
    return f"{prefix} {title or 'quiz'}".strip()


def backup_settings(src: dict, prefix: str = TITLE_PREFIX) -> dict:
    """The fields the new quiz is created with. `published` is always False."""
    out = {"title": backup_title(src.get("title"), prefix), "published": False,
           "description": NOTE + (src.get("description") or "")}
    for key in COPIED_SETTINGS:
        value = src.get(key)
        if value is None:
            continue
        if key in ("access_code", "ip_filter") and not value:
            continue
        out[key] = value
    return out


def questions_to_write(questions: list[dict]) -> list[dict]:
    """READ shape -> WRITE shape for every question; matching answers get the
    full weight the script gave them, since Canvas returns none for those."""
    out = []
    for q in questions:
        w = ContentOps.question_read_to_write(q)
        if q.get("question_type") == "matching_question":
            for a in w.get("answers") or []:
                a.setdefault("answer_weight", 100)
        if q.get("question_type") == "text_only_question":
            w.pop("answers", None)
        out.append(w)
    return out


def _points(questions: list[dict]) -> float:
    total = 0.0
    for q in questions:
        try:
            total += float(q.get("points_possible") or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


def quizzes(app, course_id) -> list[dict]:
    rows = []
    for q in app.content.quizzes_content(course_id):
        rows.append({"id": q.get("id"), "title": q.get("title"), "quiz_type": q.get("quiz_type"),
                     "published": q.get("published"), "question_count": q.get("question_count"),
                     "points_possible": q.get("points_possible"), "html_url": q.get("html_url"),
                     "is_backup": str(q.get("title") or "").startswith(TITLE_PREFIX + " ")})
    return rows


def plan(app, course_id, quiz_id) -> dict:
    """What a backup of this quiz would create. Reads only."""
    c = app.content
    src = c.quiz_content(course_id, quiz_id) or {}
    questions = c.quiz_questions(course_id, quiz_id)
    types = Counter(q.get("question_type") or "?" for q in questions)
    return {"source": {"id": src.get("id"), "title": src.get("title"), "quiz_type": src.get("quiz_type"),
                       "published": src.get("published"), "html_url": src.get("html_url"),
                       "questions": len(questions), "points": _points(questions),
                       "types": dict(types)},
            "target": backup_settings(src),
            "questions": len(questions),
            "refusal": None if questions else "This quiz has no questions, so there is nothing to back up."}


def backup(app, course_id, quiz_id, gate: Gate, log: Log = quiet_log, prefix: str = TITLE_PREFIX) -> dict:
    """Create the unpublished copy, re-post every question, verify, ledger."""
    c = app.content
    course = c.course_detail(course_id) or {}
    label = course_label(course, course_id)
    src = c.quiz_content(course_id, quiz_id) or {}
    questions = c.quiz_questions(course_id, quiz_id)
    if not questions:
        raise Refused("This quiz has no questions, so there is nothing to back up.")
    settings = backup_settings(src, prefix)
    writes = questions_to_write(questions)
    src_points = _points(questions)

    sentence = (f"Create an unpublished quiz named {settings['title']} in {label}, a copy of "
                f"{src.get('title')} with its {plural(len(questions), 'question')} ({src_points:g} points) and "
                "settings. The original is not changed. Students cannot see the copy.")
    detail = [{"label": "Questions", "from": str(len(questions)), "to": f"{len(questions)} copied"},
              {"label": "Published", "from": "yes" if src.get("published") else "no", "to": "no (the copy)"}]
    gate("courseops.quiz_backup", {"course_id": str(course_id), "quiz_id": str(quiz_id),
                                   "questions": len(questions), "title": settings["title"]},
         sentence, detail)

    log(f"creating {settings['title']} (unpublished)")
    new = c.create_quiz(course_id, **settings) or {}
    new_id = new.get("id")
    if not new_id:
        raise RuntimeError(f"Canvas did not return the new quiz: {new}")
    if new.get("published"):
        log("WARNING: Canvas reports the copy as published; it was asked for unpublished")
    ok, failed, errors = 0, 0, []
    for i, (q, w) in enumerate(zip(questions, writes), 1):
        log(f"question {i}: {q.get('question_name') or q.get('question_type')}", i, len(writes))
        try:
            c.create_quiz_question(course_id, new_id, w)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"question": q.get("question_name"), "type": q.get("question_type"),
                           "error": f"{type(exc).__name__}: {exc}"})

    # verify through /questions; the quiz object lies while unpublished
    check = c.quiz_questions(course_id, new_id)
    copy_points = _points(check)
    warnings = []
    if len(check) != len(questions):
        warnings.append(f"question count differs: copy has {len(check)}, source {len(questions)}")
    if abs(copy_points - src_points) > 0.001:
        warnings.append(f"points differ: copy {copy_points:g}, source {src_points:g}")
    published = bool(new.get("published"))
    if published:
        warnings.append("the copy is published; it should not be")
    url = new.get("html_url") or f"{app.cfg.base_url}/courses/{course_id}/quizzes/{new_id}"
    entry = {"at": now_iso(), "source_id": src.get("id"), "source_title": src.get("title"),
             "quiz_id": new_id, "title": settings["title"], "url": url,
             "questions_source": len(questions), "questions_copy": len(check),
             "points_source": src_points, "points_copy": copy_points,
             "posted_ok": ok, "posted_failed": failed, "errors": errors,
             "published": published, "warnings": warnings, "ok": not warnings and not failed}
    append_history(area_dir(app, course_id) / HISTORY, entry)
    ledger.record(app.course_dir(course_id), AREA,
                  f"Created the unpublished quiz {settings['title']} in {label} as a backup of "
                  f"{src.get('title')} ({len(check)} of {len(questions)} questions copied)",
                  url=url, count=1, kind="quiz_backup")
    log(f"RESULT: posted {ok} ok, {failed} failed; backup holds {len(check)} questions "
        f"({copy_points:g} points) vs source {len(questions)} ({src_points:g}); published={published}")
    for w in warnings:
        log(f"WARNING: {w}")
    log(f"BACKUP_QUIZ_ID={new_id}")
    return entry


def history(app, course_id) -> list[dict]:
    rows = load_json(area_dir(app, course_id) / HISTORY, []) or []
    rows.reverse()
    return rows


__all__ = ["backup_title", "backup_settings", "questions_to_write", "quizzes", "plan", "backup",
           "history", "TITLE_PREFIX"]
