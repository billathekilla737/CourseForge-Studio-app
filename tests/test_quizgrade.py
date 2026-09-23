"""A quiz's written answers are graded on top of Canvas's multiple choice."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from courseforge import gradesync, quizgrade
from courseforge.store import Store


BANK = [
    {"id": 1, "question_type": "multiple_choice_question", "points_possible": 2,
     "question_text": "Pick one", "position": 1},
    {"id": 9, "question_type": "essay_question", "points_possible": 10,
     "question_name": "Explain", "question_text": "<p>Why?</p>", "position": 2},
]


class WrittenQuestions(unittest.TestCase):
    def test_only_the_essay_is_a_criterion(self):
        manuals = quizgrade.manual_questions(BANK)
        self.assertEqual([q["id"] for q in manuals], ["9"])
        self.assertEqual(quizgrade.criteria(manuals)[0]["points"], 10)
        self.assertEqual(quizgrade.criteria(manuals)[0]["id"], "q9")

    def test_pending_review_still_needs_the_essay(self):
        self.assertTrue(quizgrade.needs_written(
            {"workflow_state": "pending_review", "score": 18}, manuals(), 28))
        self.assertFalse(quizgrade.needs_written(
            {"workflow_state": "complete", "score": 28}, manuals(), 28))

    def test_the_attempt_has_the_choice_and_the_essay(self):
        bank = [
            {"id": 1, "position": 1, "question_type": "multiple_choice_question",
             "question_name": "Question 1", "question_text": "<p>Which loop?</p>",
             "points_possible": 2, "answers": [
                 {"id": 10, "text": "The game loop"},
                 {"id": 11, "text": "A for loop"},
             ]},
            {"id": 9, "position": 2, "question_type": "essay_question",
             "question_name": "Explain", "question_text": "<p>Why?</p>",
             "points_possible": 8},
        ]
        rows = quizgrade.review_rows(bank, [
            {"question_id": 1, "answer_id": 10, "correct": True, "points": 2},
            {"question_id": 9, "text": "<p>Because the loop repeats.</p>", "points": 0,
             "correct": "undefined"},
        ])
        self.assertEqual(rows[0]["response"], "The game loop")
        self.assertTrue(rows[0]["correct"])
        self.assertIn("Because the loop repeats.", rows[1]["response"])
        self.assertTrue(rows[1]["manual"])
        self.assertNotIn("<p>", rows[1]["response"])

    def test_written_points_add_to_the_multiple_choice(self):
        folded = quizgrade.fold_auto(
            {"scores": {"q9": 8}, "total": 8, "flags": []},
            {"auto_score": 18, "quiz_id": "3", "submission_id": 4, "attempt": 1,
             "questions": manuals()})
        self.assertEqual(folded["total"], 26)
        self.assertEqual(folded["scores"]["_quiz_auto"], 18)
        self.assertEqual(folded["quiz_question_scores"]["9"], 8)


def manuals():
    return quizgrade.manual_questions(BANK)


class PartialQuizIsNotAdopted(unittest.TestCase):
    def test_canvas_multiple_choice_does_not_count_as_finished(self):
        store = Store(Path(tempfile.mkdtemp()))
        store.save_draft("1", "2", {"students": {}, "rubric": [], "points_possible": 28})
        extracted = {
            "7": {"canvas_score": 18, "status": "graded", "quiz_needs_written": True,
                  "canvas_rubric": {}},
        }
        out = gradesync.merge_canvas_grades(store, "1", "2", extracted)
        self.assertIn("7", out["quiz_open"])
        self.assertNotIn("7", out["adopted"])
        self.assertIsNone(store.draft("1", "2")["students"].get("7"))


if __name__ == "__main__":
    unittest.main()
