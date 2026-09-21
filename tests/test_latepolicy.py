"""Late-work rules read from a syllabus and applied after auto-grading."""
from __future__ import annotations

import unittest

from courseforge import curve, latepolicy


SYLLABUS = """
Course outcomes. Students will identify the parts of a game loop.

Late Work
Late assignments are accepted for up to 7 days. A 10% penalty is taken
per day. Work more than 7 days late is not accepted.

Academic integrity. Do not copy another student's file.
"""


class ParseSyllabus(unittest.TestCase):
    def test_percent_per_day_is_the_section_not_the_opening(self):
        passage = latepolicy.extract_late_passages(SYLLABUS)
        self.assertIn("10% penalty", passage)
        self.assertNotIn("game loop", passage[:40])
        policy = latepolicy.parse(SYLLABUS)
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertEqual(policy["percent"], 10.0)
        self.assertEqual(policy["interval"], "day")

    def test_no_late_work(self):
        policy = latepolicy.parse("Late work will not be accepted in this course.")
        self.assertEqual(policy["kind"], "none_accepted")

    def test_flat_half_credit(self):
        policy = latepolicy.parse("If it is late, the student receives 50% credit.")
        self.assertEqual(policy["kind"], "flat_percent")
        self.assertEqual(policy["percent"], 50.0)

    def test_grace_period_and_hourly(self):
        policy = latepolicy.parse(
            "There is a 24 hour grace period. After that, 5% per hour late.")
        self.assertEqual(policy["kind"], "percent_per_hour")
        self.assertEqual(policy["percent"], 5.0)
        self.assertEqual(policy["grace_hours"], 24.0)

    def test_no_mention_is_empty(self):
        policy = latepolicy.parse("Office hours are Tuesday at 2.")
        self.assertEqual(policy["kind"], "none")

    def test_letter_grade_per_day_is_ten_percent(self):
        policy = latepolicy.parse("Late work loses one letter grade per day.")
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertEqual(policy["percent"], 10.0)


class ApplyToAScore(unittest.TestCase):
    def test_one_day_at_ten_percent(self):
        policy = latepolicy.parse("10% per day late.")
        graded = {"total": 20, "scores": {"c1": 20}, "flags": []}
        out = latepolicy.attach(graded, {"late": True, "seconds_late": 86400}, policy)
        self.assertTrue(out["late_penalty"]["applied"])
        self.assertEqual(out["late_penalty"]["units"], 1)
        self.assertEqual(out["late_penalty"]["points"], 2.0)
        entry = {**out, "scores": {"c1": 20}}
        self.assertEqual(curve.final_total(entry, [{"id": "c1", "points": 20}], 20), 18)

    def test_a_second_late_rounds_up_to_a_day(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 10, "flags": []},
            {"late": True, "seconds_late": 30}, policy)
        self.assertEqual(out["late_penalty"]["units"], 1)
        self.assertEqual(out["late_penalty"]["points"], 1.0)

    def test_grace_period_does_not_dock(self):
        policy = latepolicy.parse("24 hour grace period. Then 10% per day late.")
        out = latepolicy.attach(
            {"total": 20, "flags": []},
            {"late": True, "seconds_late": 3600}, policy)
        self.assertFalse(out["late_penalty"]["applied"])

    def test_not_accepted_zeros_the_score(self):
        policy = latepolicy.parse("No late work is accepted.")
        out = latepolicy.attach(
            {"total": 16, "flags": []},
            {"late": True, "seconds_late": 100}, policy)
        self.assertTrue(out["late_penalty"]["applied"])
        self.assertEqual(out["late_penalty"]["points"], 16)
        self.assertEqual(curve.final_total(out, [], 20), 0)

    def test_canvas_already_deducts_so_studio_does_not(self):
        policy = latepolicy.parse("10% per day late.")
        policy["canvas_applies"] = True
        policy["kind"] = "canvas"
        policy["summary"] = "Canvas already deducts."
        out = latepolicy.attach(
            {"total": 20, "flags": []},
            {"late": True, "seconds_late": 86400}, policy)
        self.assertFalse(out["late_penalty"]["applied"])
        self.assertEqual(curve.final_total(
            {**out, "scores": {"c1": 20}}, [{"id": "c1", "points": 20}], 20), 20)

    def test_on_time_is_untouched(self):
        policy = latepolicy.parse("10% per day late.")
        graded = {"total": 20, "flags": []}
        out = latepolicy.attach(graded, {"late": False, "seconds_late": 0}, policy)
        self.assertNotIn("late_penalty", out)

    def test_a_curve_sits_on_top_of_the_late_dock(self):
        entry = {
            "total": 10, "scores": {"c1": 10},
            "curve": {"flat": 2, "by_criterion": {}},
            "late_penalty": {"applied": True, "kind": "percent_per_day",
                             "percent": 10, "units": 1, "interval": "day",
                             "floor_percent": 0, "points": 1},
        }
        self.assertEqual(curve.final_total(entry, [{"id": "c1", "points": 20}], 20), 11)


class LoadFromAFakeCourse(unittest.TestCase):
    def test_syllabus_body_is_enough(self):
        class Client:
            def course_detail(self, cid, include=None):
                return {"syllabus_body": "<p>" + SYLLABUS + "</p>"}

            def pages(self, cid):
                return []

            def course_late_policy(self, cid):
                return None

        policy = latepolicy.load(Client(), "1", lambda html: html)
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertFalse(policy["canvas_applies"])

    def test_canvas_policy_wins_over_the_syllabus(self):
        class Client:
            def course_detail(self, cid, include=None):
                return {"syllabus_body": "10% per day late."}

            def pages(self, cid):
                return []

            def course_late_policy(self, cid):
                return {"late_submission_deduction_enabled": True,
                        "late_submission_deduction": 10,
                        "late_submission_interval": "day"}

        policy = latepolicy.load(Client(), "1", lambda html: html)
        self.assertTrue(policy["canvas_applies"])
        self.assertEqual(policy["kind"], "canvas")


if __name__ == "__main__":
    unittest.main()
