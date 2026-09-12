"""Validate quiz questions before they reach Canvas (library and CLI).

Ported from the old toolkit's check_quiz.py and widened to the three shapes a
question shows up in around here:

- manifest shape       {text, type, points, answers: [{text, correct}]}
- toolkit shape        {name, text, answers: [["Correct", 100], ["Wrong", 0]]}
- Canvas WRITE shape   {question_text, question_type, points_possible,
                        answers: [{answer_text, answer_weight}]}

The expensive mistake it catches: a multiple-choice question with zero or more
than one correct answer, which Canvas accepts silently and no student can
answer. It also checks the count and point total when asked, because Canvas
recomputes a quiz's total from its questions.

    report = check_quiz.check(questions, expect_count=None)
    report["ok"], report["failed"], report["points"]

    python -m courseforge.content.check_quiz quiz.json [--expect-count N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ALLOWED_TYPES = {
    "multiple_choice_question", "true_false_question", "short_answer_question",
    "essay_question", "multiple_answers_question", "fill_in_multiple_blanks_question",
    "matching_question", "numerical_question", "text_only_question",
}
NEEDS_ONE_CORRECT = {"multiple_choice_question", "true_false_question"}
NO_ANSWERS = {"essay_question", "text_only_question"}


def normalize(q: dict) -> dict:
    """Any of the three shapes -> {name, text, type, points, answers:[{text, weight}]}."""
    text = q.get("text") or q.get("question_text") or ""
    qtype = q.get("type") or q.get("question_type") or "multiple_choice_question"
    points = q.get("points", q.get("points_possible", 1))
    try:
        points = float(points) if points is not None else 1.0
    except (TypeError, ValueError):
        points = 1.0
    answers: list[dict] = []
    for a in q.get("answers") or []:
        if isinstance(a, (list, tuple)):
            atext = str(a[0]) if a else ""
            try:
                weight = float(a[1]) if len(a) > 1 and a[1] is not None else 0.0
            except (TypeError, ValueError):
                weight = 0.0
        elif isinstance(a, dict):
            atext = a.get("text", a.get("answer_text", ""))
            if "correct" in a:
                weight = 100.0 if a.get("correct") else 0.0
            else:
                raw = a.get("weight", a.get("answer_weight", 0))
                try:
                    weight = float(raw or 0)
                except (TypeError, ValueError):
                    weight = 0.0
        else:
            atext, weight = str(a), 0.0
        answers.append({"text": str(atext or ""), "weight": weight})
    return {"name": q.get("name") or q.get("question_name") or "", "text": str(text),
            "type": qtype, "points": points, "answers": answers}


def to_write_shape(q: dict, index: int) -> dict:
    """The manifest/normalized question as Canvas accepts it (answer_text / answer_weight)."""
    n = normalize(q)
    out = {"question_name": n["name"] or f"Question {index}",
           "question_text": n["text"], "question_type": n["type"],
           "points_possible": n["points"]}
    if n["type"] not in NO_ANSWERS and n["answers"]:
        out["answers"] = [{"answer_text": a["text"], "answer_weight": int(a["weight"])}
                          for a in n["answers"]]
    return out


def check(questions: list[dict], expect_count: int | None = None,
          expect_options: int | None = None) -> dict:
    passed: list[str] = []
    failed: list[str] = []
    warned: list[str] = []
    total = 0.0
    seen_names: Counter = Counter()

    if expect_count is not None:
        if len(questions) == expect_count:
            passed.append(f"question count matches {expect_count}")
        else:
            failed.append(f"question count is {len(questions)}, expected {expect_count} "
                          "(this changes the quiz's point total)")
    if not questions:
        failed.append("no questions")

    for i, raw in enumerate(questions, 1):
        q = normalize(raw)
        label = q["name"] or f"question {i}"
        seen_names[label] += 1
        total += q["points"]
        if q["type"] not in ALLOWED_TYPES:
            failed.append(f"{label}: unknown question type {q['type']!r}")
            continue
        plain = re.sub(r"<[^>]+>", "", q["text"]).strip()
        if not plain:
            failed.append(f"{label}: question text is empty")
        if q["points"] < 0:
            failed.append(f"{label}: negative points")
        answers = q["answers"]
        correct = [a for a in answers if a["weight"] > 0]
        blank = [a for a in answers if not a["text"].strip()]
        if blank and q["type"] not in NO_ANSWERS:
            failed.append(f"{label}: {len(blank)} answer(s) with empty text")
        if q["type"] in NO_ANSWERS:
            if answers:
                warned.append(f"{label}: answers listed on a {q['type']} are ignored")
            else:
                passed.append(f"{label}: {q['type']}, graded by hand")
            continue
        if expect_options and len(answers) != expect_options:
            failed.append(f"{label}: has {len(answers)} answer option(s), expected {expect_options}")
        if q["type"] in NEEDS_ONE_CORRECT:
            if q["type"] == "true_false_question" and len(answers) != 2:
                failed.append(f"{label}: true/false needs exactly 2 answers, has {len(answers)}")
            if len(answers) < 2:
                failed.append(f"{label}: needs at least 2 answer options")
            if len(correct) == 0:
                failed.append(f"{label}: no answer is marked correct (unanswerable)")
            elif len(correct) > 1:
                failed.append(f"{label}: {len(correct)} answers marked correct (must be exactly 1): "
                              f"{[c['text'][:40] for c in correct]}")
            else:
                passed.append(f"{label}: exactly one correct answer -> {correct[0]['text'][:60]!r}")
        elif q["type"] == "short_answer_question":
            if not answers:
                failed.append(f"{label}: fill-in-the-blank needs 1 to 3 accepted answers")
            elif len(answers) > 3:
                warned.append(f"{label}: {len(answers)} accepted answers; 1 to 3 is usual")
            elif len(correct) != len(answers):
                failed.append(f"{label}: every listed answer on a fill-in-the-blank must be correct")
            else:
                passed.append(f"{label}: {len(answers)} accepted answer(s)")
        elif q["type"] == "multiple_answers_question":
            if not correct:
                failed.append(f"{label}: no answer is marked correct")
            elif len(correct) == len(answers):
                warned.append(f"{label}: every answer is correct")
            else:
                passed.append(f"{label}: {len(correct)} of {len(answers)} correct")
        else:
            passed.append(f"{label}: {q['type']} not checked beyond text and points")

    dupes = [name for name, n in seen_names.items() if n > 1]
    if dupes:
        failed.append(f"duplicate question name(s): {dupes}")
    elif questions:
        passed.append("all question names are unique")

    return {"ok": not failed, "passed": passed, "failed": failed, "warned": warned,
            "count": len(questions), "points": total}


def load_questions(data) -> list[dict]:
    if isinstance(data, dict):
        if "questions" in data:
            return list(data["questions"] or [])
        if "quiz" in data and isinstance(data["quiz"], dict):
            return list(data["quiz"].get("questions") or [])
    if isinstance(data, list):
        return data
    raise ValueError("expected a JSON array of questions or an object with a questions list")


def print_report(report: dict, out=sys.stdout) -> None:
    print(f"questions found: {report['count']}  points: {report['points']:g}", file=out)
    for msg in report["passed"]:
        print("  PASS  " + msg, file=out)
    for msg in report["warned"]:
        print("  WARN  " + msg, file=out)
    for msg in report["failed"]:
        print("  FAIL  " + msg, file=out)
    print(file=out)
    print(f"RESULT: {len(report['passed'])} passed, {len(report['failed'])} failed", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate quiz questions before they reach Canvas.")
    ap.add_argument("quiz_file")
    ap.add_argument("--expect-count", type=int, default=None)
    ap.add_argument("--expect-options", type=int, default=None,
                    help="expected number of answer options per question")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.quiz_file).read_text(encoding="utf-8"))
    report = check(load_questions(data), args.expect_count, args.expect_options)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
