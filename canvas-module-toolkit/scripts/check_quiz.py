#!/usr/bin/env python3
"""
check_quiz.py - validates a quiz questions JSON file before it ever reaches Canvas.

Catches the single most expensive mistake in this whole workflow: a multiple-choice
question with zero or more than one answer marked correct, which silently produces a
quiz question that no student can answer correctly (or that awards credit for a wrong
answer). Also checks the question count matches what you expect, since Canvas quiz
points are 1-per-question by default - deleting and recreating with a different count
silently changes the quiz's total points.

Schema (a JSON array, or {"questions": [...]}), matching what Get-CanvasModule.ps1
dumps and what Push-CanvasModule.ps1's questions_file expects:
  [
    { "name": "Q1 ...", "text": "<p>...</p>", "incorrect": "feedback on a wrong pick",
      "answers": [["Correct option", 100], ["Wrong option", 0], ["Wrong", 0], ["Wrong", 0]] }
  ]

Usage:
    python3 check_quiz.py quiz.json
    python3 check_quiz.py quiz.json --expect-count 14
    python3 check_quiz.py quiz.json --expect-options 4
"""
import argparse
import json
import re
import sys
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("quiz_file")
    ap.add_argument("--expect-count", type=int, default=None,
                     help="fail if the question count does not match (use this to guarantee point totals don't drift)")
    ap.add_argument("--expect-options", type=int, default=4,
                     help="expected number of answer options per question (default 4; 0 disables the check)")
    args = ap.parse_args()

    data = json.load(open(args.quiz_file, encoding="utf-8"))
    questions = data["questions"] if isinstance(data, dict) and "questions" in data else data

    passed, failed = 0, 0

    def ok(msg):
        nonlocal passed
        passed += 1
        print("  PASS  " + msg)

    def bad(msg):
        nonlocal failed
        failed += 1
        print("  FAIL  " + msg)

    print("questions found: %d" % len(questions))
    if args.expect_count is not None:
        if len(questions) == args.expect_count:
            ok("question count matches --expect-count=%d" % args.expect_count)
        else:
            bad("question count is %d, expected %d (this WILL change the quiz's point total)" % (len(questions), args.expect_count))

    seen_names = Counter()
    for i, q in enumerate(questions, 1):
        label = q.get("name") or ("question #%d" % i)
        seen_names[label] += 1

        answers = q.get("answers") or []
        if args.expect_options and len(answers) != args.expect_options:
            bad("%s: has %d answer option(s), expected %d" % (label, len(answers), args.expect_options))

        correct = [a for a in answers if len(a) > 1 and a[1] and int(a[1]) > 0]
        if len(correct) == 0:
            bad("%s: NO answer is marked correct - unanswerable question" % label)
        elif len(correct) > 1:
            bad("%s: %d answers marked correct (must be exactly 1) - %s" %
                (label, len(correct), [c[0][:40] for c in correct]))
        else:
            ok("%s: exactly one correct answer -> %r" % (label, correct[0][0][:60]))

        text = re.sub(r"<[^>]+>", "", q.get("text") or "")
        if not text.strip():
            bad("%s: question text is empty" % label)

    dupes = [name for name, n in seen_names.items() if n > 1]
    if dupes:
        bad("duplicate question name(s), which makes results hard to audit later: %s" % dupes)
    elif questions:
        ok("all question names are unique")

    print()
    print("RESULT: %d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
