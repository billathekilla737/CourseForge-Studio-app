"""Written answers on a Canvas quiz, which the assignment submission does not carry.

A quiz of multiple choice plus an essay comes back as an assignment with a
score and an empty body. Canvas already scored the multiple choice. The essay
lives on the quiz submission, and until someone scores it the quiz stays in
pending review. This module reads those answers so the auto-grader can score
only them and add that to the score Canvas already has.
"""
from __future__ import annotations

from .extract import html_to_text

# Canvas leaves these out of the automatic score until a person grades them.
MANUAL_TYPES = {"essay_question", "file_upload_question"}


def is_quiz(assignment: dict | None) -> bool:
    if not assignment:
        return False
    if assignment.get("quiz_id"):
        return True
    return "online_quiz" in (assignment.get("submission_types") or [])


def manual_questions(questions: list[dict] | None) -> list[dict]:
    """Essay and file-upload questions, in the order Canvas numbered them."""
    out = []
    for q in questions or []:
        if (q.get("question_type") or "") not in MANUAL_TYPES:
            continue
        try:
            points = float(q.get("points_possible") or 0)
        except (TypeError, ValueError):
            points = 0.0
        out.append({
            "id": str(q.get("id") or ""),
            "name": q.get("question_name") or "Written response",
            "position": q.get("position"),
            "points": points,
            "prompt": html_to_text(q.get("question_text") or "")[:4000],
        })
    out.sort(key=lambda q: (q.get("position") is None, q.get("position") or 0))
    return [q for q in out if q["id"]]


def criteria(questions: list[dict]) -> list[dict]:
    """One rubric row per written question. Ids are q<id>, not Canvas rubric ids."""
    rows = []
    for q in questions:
        rows.append({
            "id": f"q{q['id']}",
            "label": q.get("name") or "Written response",
            "detail": q.get("prompt") or "",
            "points": float(q.get("points") or 0),
            "ratings": [],
        })
    return rows


def class_rubric(auto_possible: float, questions: list[dict]) -> list[dict]:
    """The rows the review pane shows: Canvas's part, locked, then each essay."""
    rows = []
    if auto_possible and auto_possible > 0:
        rows.append({
            "id": "_quiz_auto",
            "label": "Already scored by Canvas",
            "detail": "Multiple choice and the other automatic questions. "
                      "Not graded again.",
            "points": float(auto_possible),
            "ratings": [],
            "locked": True,
        })
    rows.extend(criteria(questions))
    return rows


def _marked(value):
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return None


def _choice_text(question: dict, answer_id) -> str:
    if answer_id is None:
        return ""
    for ans in question.get("answers") or []:
        if str(ans.get("id")) == str(answer_id):
            return html_to_text(ans.get("html") or ans.get("text") or "")
    return ""


def _quiz_stub(part: dict) -> bool:
    text = str((part or {}).get("text") or "").strip()
    return text.startswith("- user:") and "quiz:" in text


def _submission_rows(submission: dict) -> list[dict]:
    """The attempt Canvas actually stored, not the empty assignment body."""
    history = submission.get("submission_history") or []
    history = [h for h in history if isinstance(h, dict)]
    history.sort(key=lambda h: h.get("attempt") or 0)
    for row in reversed(history):
        data = row.get("submission_data")
        if isinstance(data, list) and data:
            return data
    data = submission.get("submission_data")
    return data if isinstance(data, list) else []


def review_rows(bank: list[dict], rows: list[dict]) -> list[dict]:
    """Every question, the student's choice or essay, and the points Canvas gave."""
    by_q = {str(r.get("question_id")): r for r in rows or [] if r.get("question_id") is not None}
    ordered = sorted(bank or [], key=lambda q: (q.get("position") is None, q.get("position") or 0))
    out = []
    for q in ordered:
        got = by_q.get(str(q.get("id"))) or {}
        kind = q.get("question_type") or ""
        manual = kind in MANUAL_TYPES
        if manual:
            response = html_to_text(got.get("text") or "") or "(no written answer)"
        else:
            response = _choice_text(q, got.get("answer_id")) or "(no choice recorded)"
        out.append({
            "id": str(q.get("id") or ""),
            "position": q.get("position"),
            "name": q.get("question_name") or "",
            "type": kind,
            "prompt": html_to_text(q.get("question_text") or "")[:4000],
            "response": response[:12000],
            "points": got.get("points"),
            "points_possible": q.get("points_possible"),
            "correct": _marked(got.get("correct")),
            "manual": manual,
        })
    return [row for row in out if row["id"]]


def written_text(questions: list[dict], answers: list[dict] | None) -> str:
    """The prompt and the student's words for each written question."""
    by_id = {str(a.get("id")): a for a in (answers or []) if a.get("id") is not None}
    chunks = []
    for q in questions:
        ans = by_id.get(q["id"]) or {}
        raw = ans.get("answer")
        if isinstance(raw, list):
            raw = " ".join(str(part) for part in raw)
        text = html_to_text(str(raw or "")) or "(no written answer)"
        pos = q.get("position") or ""
        chunks.append(
            f"## Written question {pos}: {q.get('name') or 'Essay'}\n"
            f"Worth {q.get('points')} points. Grade this question only.\n"
            f"Prompt:\n{q.get('prompt') or '(no prompt)'}\n\n"
            f"Student answer:\n{text[:12000]}"
        )
    return "\n\n".join(chunks).strip()


def needs_written(quiz_submission: dict | None, questions: list[dict],
                  points_possible: float) -> bool:
    """True when the essay is not already inside Canvas's score.

    pending_review is Canvas's own word for that. A complete quiz already
    includes the written score, and adding it again would double it.
    """
    if not questions:
        return False
    state = str((quiz_submission or {}).get("workflow_state") or "").lower()
    if state == "complete":
        return False
    if state == "pending_review":
        return True
    try:
        score = float((quiz_submission or {}).get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    manual = sum(float(q.get("points") or 0) for q in questions)
    # The score Canvas is holding is no bigger than the automatic part,
    # so the written points are not in it yet.
    return points_possible <= 0 or score <= (points_possible - manual) + 0.05


def fold_auto(result: dict, quiz: dict) -> dict:
    """Add Canvas's automatic score on top of the written points just awarded."""
    auto = float(quiz.get("auto_score") or 0)
    scores = dict(result.get("scores") or {})
    scores["_quiz_auto"] = auto
    written = round(sum(v for k, v in scores.items() if k != "_quiz_auto"), 2)
    result = dict(result)
    result["scores"] = scores
    result["quiz_auto_score"] = auto
    result["quiz_written_score"] = written
    result["total"] = round(auto + written, 2)
    result["quiz_id"] = quiz.get("quiz_id")
    result["quiz_submission_id"] = quiz.get("submission_id")
    result["quiz_attempt"] = quiz.get("attempt") or 1
    result["quiz_question_scores"] = {
        q["id"]: scores.get(f"q{q['id']}")
        for q in (quiz.get("questions") or [])
    }
    flags = list(result.get("flags") or [])
    flags.append(f"multiple choice already scored by Canvas ({auto:g})")
    result["flags"] = flags
    return result


def prepare(client, course_id, quiz_id, assignment: dict | None = None) -> dict | None:
    """Question bank plus one quiz-submission row per student. None if no essay."""
    try:
        bank = client.quiz_questions(course_id, quiz_id)
    except Exception:  # noqa: BLE001
        return None
    bank = bank if isinstance(bank, list) else []
    manuals = manual_questions(bank)
    if not manuals:
        return None
    try:
        rows = client.quiz_submissions(course_id, quiz_id)
    except Exception:  # noqa: BLE001
        rows = []
    by_user = {}
    for row in rows or []:
        uid = str(row.get("user_id") or "")
        if uid:
            by_user[uid] = row
    try:
        possible = float((assignment or {}).get("points_possible") or 0)
    except (TypeError, ValueError):
        possible = 0.0
    manual_points = sum(float(q.get("points") or 0) for q in manuals)
    auto_possible = max(0.0, possible - manual_points)
    return {
        "quiz_id": str(quiz_id),
        "bank": bank,
        "questions": manuals,
        "by_user": by_user,
        "auto_possible": auto_possible,
        "points_possible": possible,
        "criteria": class_rubric(auto_possible, manuals),
    }


def attach(entry: dict, pack: dict, submission: dict) -> None:
    """Read this student's quiz attempt onto the extracted entry.

    The assignment body is only a one-line stub (`user, quiz, score`). The
    choices and the essay are on the attempt's submission_data.
    """
    uid = str(entry.get("user_id") or "")
    qsub = (pack.get("by_user") or {}).get(uid) or {}
    review = review_rows(pack.get("bank") or [], _submission_rows(submission))
    if not review:
        return
    entry["quiz_review"] = review
    questions = pack.get("questions") or []
    written = [row for row in review if row.get("manual")]
    chunks = []
    for row in written:
        chunks.append(
            f"## Written question {row.get('position') or ''}: {row.get('name') or 'Essay'}\n"
            f"Worth {row.get('points_possible')} points. Grade this question only.\n"
            f"Prompt:\n{row.get('prompt') or '(no prompt)'}\n\n"
            f"Student answer:\n{row.get('response') or '(no written answer)'}"
        )
    text = "\n\n".join(chunks).strip()
    if text:
        entry["text"] = text
        entry["body_text"] = text
        entry["words"] = len(text.split())
        entry["parts"] = [p for p in (entry.get("parts") or []) if not _quiz_stub(p)]
    if not qsub.get("id"):
        qsub = {
            "score": submission.get("score"),
            "workflow_state": submission.get("workflow_state"),
            "attempt": submission.get("attempt") or 1,
        }
    try:
        auto = float(qsub.get("score") or submission.get("score") or 0)
    except (TypeError, ValueError):
        auto = 0.0
    open_written = needs_written(qsub, questions, float(pack.get("points_possible") or 0))
    entry["quiz"] = {
        "quiz_id": pack.get("quiz_id"),
        "submission_id": qsub.get("id"),
        "attempt": qsub.get("attempt") or 1,
        "auto_score": auto if open_written else 0.0,
        "workflow": qsub.get("workflow_state") or "",
        "questions": questions,
        "written_included": not open_written,
    }
    if open_written:
        entry["quiz_needs_written"] = True
        entry["quiz_auto_score"] = auto
