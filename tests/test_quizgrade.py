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


class AnAutomaticQuizWasSubmitted(unittest.TestCase):
    def test_a_quiz_attempt_without_a_timestamp_still_counts(self):
        self.assertTrue(quizgrade.was_submitted({"submission_type": "online_quiz", "score": 8}))
        self.assertTrue(quizgrade.was_submitted({
            "submission_history": [{"submission_data": [{"question_id": 1, "answer_id": 2}]}],
        }))

    def test_a_publisher_quiz_with_a_canvas_score_counts(self):
        asg = {"submission_types": ["external_tool"]}
        self.assertTrue(quizgrade.was_submitted({
            "workflow_state": "graded", "score": 4, "missing": False,
        }, asg))
        self.assertFalse(quizgrade.was_submitted({
            "workflow_state": "graded", "score": 0, "missing": True,
        }, asg))

    def test_a_posted_zero_with_no_attempt_stays_unsubmitted(self):
        self.assertFalse(quizgrade.was_submitted({
            "workflow_state": "graded", "score": 0, "submission_type": None,
        }))


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


class AFinishedAssignmentIsGraded(unittest.TestCase):
    def test_pending_written_answers_are_not_graded(self):
        extracted = {
            "7": {"status": "pending_review", "canvas_score": 18,
                  "canvas_posted_at": "2026-09-19T00:00:00Z",
                  "quiz_needs_written": True},
        }
        self.assertFalse(gradesync.assignment_is_graded(1, extracted, True))
        self.assertFalse(gradesync.assignment_is_graded(0, extracted, True))

    def test_scored_submissions_with_nothing_waiting_are_graded(self):
        extracted = {
            "7": {"status": "graded", "canvas_score": 90,
                  "canvas_posted_at": "2026-09-19T00:00:00Z"},
            "8": {"status": "unsubmitted"},
        }
        self.assertTrue(gradesync.assignment_is_graded(0, extracted, True))

    def test_other_edges_around_a_blank_written_answer(self):
        blank = {"quiz_review": [
            {"manual": True, "id": "9", "type": "essay_question", "response": "n/a"},
            {"manual": True, "id": "10", "type": "essay_question", "response": ""},
        ]}
        zeros = {"needs_human": True, "source": "claude", "total": 35,
                 "scores": {"q9": 0, "q10": 0},
                 "quiz_question_scores": {"9": None, "10": 0}}
        self.assertEqual(quizgrade.question_scores(zeros), {"10": 0, "9": 0})
        upload = {"manual": True, "id": "3", "type": "file_upload_question", "response": ""}
        self.assertFalse(quizgrade.answer_is_blank("", upload))
        self.assertFalse(quizgrade.written_answers_blank(
            {"quiz_review": [upload, blank["quiz_review"][0]]}))
        mixed = {"quiz_review": [
            {"manual": True, "id": "9", "response": "n/a"},
            {"manual": True, "id": "10", "response": "a real answer"},
        ]}
        scored = {"needs_human": True, "source": "claude", "total": 40,
                  "scores": {"q9": 0, "q10": 5}}
        self.assertTrue(quizgrade.hold_for_review(scored, mixed))
        missing = {"needs_human": True, "source": "claude", "total": 35,
                   "scores": {"q9": 0}}
        self.assertTrue(quizgrade.hold_for_review(missing, blank))
        flagged = {"user_id": "8", "status": "pending_review", "canvas_score": 80,
                   "canvas_posted_at": "2026-09-24T12:00:00",
                   "quiz_auto_score": 50, "quiz_needs_written": True,
                   "quiz_review": mixed["quiz_review"]}
        self.assertFalse(gradesync.assignment_is_graded(
            1, {"8": flagged}, True, {"8": scored}))

    def test_a_blank_answer_scored_zero_is_graded(self):
        info = {"quiz_review": [
            {"manual": True, "id": "9", "response": "n/a"},
            {"manual": True, "id": "10", "response": ""},
        ]}
        entry = {"needs_human": True, "source": "claude", "total": 35,
                 "scores": {"q9": 0, "q10": 0, "_quiz_auto": 35}}
        self.assertTrue(quizgrade.written_answers_blank(info))
        self.assertTrue(quizgrade.written_scores_recorded(entry, info))
        self.assertFalse(quizgrade.hold_for_review(entry, info))
        self.assertTrue(gradesync.assignment_is_graded(2, {
            "7": {"user_id": "7", "status": "pending_review", "canvas_score": 35,
                  "canvas_posted_at": "2026-09-24T12:00:00",
                  "quiz_auto_score": 35, "quiz_needs_written": True,
                  "quiz_review": info["quiz_review"]},
        }, True, {"7": entry}))

    def test_a_posted_total_finishes_a_quiz_canvas_still_calls_open(self):
        extracted = {
            "7": {"user_id": "7", "status": "pending_review", "canvas_score": 150,
                  "canvas_posted_at": "2026-09-24T12:00:00",
                  "quiz_auto_score": 70, "quiz_needs_written": True},
        }
        students = {"7": {"total": 112, "final_total": 150, "source": "claude"}}
        self.assertTrue(gradesync.assignment_is_graded(
            10, extracted, True, students))
        waiting = {
            "7": {"user_id": "7", "status": "pending_review", "canvas_score": 70,
                  "canvas_posted_at": "2026-09-24T12:00:00",
                  "quiz_auto_score": 70, "quiz_needs_written": True},
        }
        self.assertFalse(gradesync.assignment_is_graded(
            10, waiting, True, students))

    def test_a_list_refresh_marks_a_publisher_quiz_graded(self):
        self.assertTrue(gradesync.assignment_is_graded(
            0, {}, False, graded_submissions_exist=True))
        self.assertFalse(gradesync.assignment_is_graded(0, {}, False))
        self.assertFalse(gradesync.assignment_is_graded(
            2, {}, True, graded_submissions_exist=True))

    def test_nothing_turned_in_is_not_graded(self):
        self.assertFalse(gradesync.assignment_is_graded(0, {}, False))
        self.assertTrue(gradesync.assignment_is_graded(
            0, {}, True))


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
