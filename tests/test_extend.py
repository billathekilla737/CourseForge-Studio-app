"""Deadline extensions: the arithmetic, and the rows it refuses to touch.

Everything here is about one question -- if a student is given three more days,
what date do they actually end up with -- and the ways that question has a
wrong answer that nobody notices: the wrong timezone, the wrong starting date,
a lock date left behind, or somebody else's deadline moved as a side effect.
"""
import unittest
from datetime import datetime, timezone

from courseforge import extend

TZ = "America/Chicago"
# Sep 8 2026, 11:59 pm Central.
SEP8 = "2026-09-09T04:59:00Z"
# Oct 31 2026, 11:59 pm Central -- three days later crosses the end of DST.
OCT31 = "2026-11-01T04:59:00Z"


def student(uid="7", name="Jane Doe"):
    return {"user_id": uid, "name": name}


def target(**kw):
    base = {"course_id": "1", "course_label": "ENG 1113", "time_zone": TZ,
            "assignment_id": "10", "title": "Essay 2", "kind": "assignment",
            "due_at": SEP8, "lock_at": None, "html_url": "",
            "overrides": [], "enrolled": {"7"}, "sections": {"7": ["100"]}}
    base.update(kw)
    return base


class TheClockTheDatesAreOn(unittest.TestCase):
    """The tool ran on a machine with no IANA database and silently fell back
    to UTC, which moves an 11:59 pm deadline into the next calendar day."""

    def test_a_late_evening_deadline_keeps_its_own_day(self):
        self.assertTrue(extend.day_in_window(SEP8, "2026-09-08", "2026-09-08", TZ))
        self.assertFalse(extend.day_in_window(SEP8, "2026-09-09", "2026-09-09", TZ))

    def test_the_window_includes_both_end_days(self):
        self.assertTrue(extend.day_in_window(SEP8, "2026-09-01", "2026-09-08", TZ))
        self.assertTrue(extend.day_in_window(SEP8, "2026-09-08", "2026-09-15", TZ))

    def test_the_wall_clock_time_survives_the_shift(self):
        self.assertEqual(extend.shift(SEP8, 3, TZ), "2026-09-12T04:59:00Z")

    def test_crossing_daylight_saving_keeps_eleven_fifty_nine(self):
        """+3 days across the end of DST is 72 hours plus one, not 72."""
        self.assertEqual(extend.shift(OCT31, 3, TZ), "2026-11-04T05:59:00Z")

    def test_the_zone_actually_used_is_reported(self):
        note = extend.zone_note(TZ)
        self.assertTrue(note, "the page has nothing to show")
        # Either the real zone, or an honest sentence about why it is not.
        if note != TZ:
            self.assertIn("tzdata", note)

    def test_nothing_shifts_a_missing_date(self):
        self.assertIsNone(extend.shift(None, 3, TZ))
        self.assertIsNone(extend.shift("", 3, TZ))


class WhichDateTheStudentIsActuallyHeldTo(unittest.TestCase):
    def test_with_no_overrides_it_is_the_class_date(self):
        out = extend.effective(target(), [], "7", ["100"])
        self.assertEqual(out["due_at"], SEP8)
        self.assertEqual(out["source"], "the class due date")
        self.assertIsNone(out["override_id"])

    def test_a_section_override_wins_over_the_class_date(self):
        overrides = [{"id": 5, "course_section_id": 100, "due_at": "2026-09-11T04:59:00Z"}]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertEqual(out["due_at"], "2026-09-11T04:59:00Z")
        self.assertIn("section", out["source"])

    def test_another_sections_override_is_ignored(self):
        overrides = [{"id": 5, "course_section_id": 999, "due_at": "2026-09-11T04:59:00Z"}]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertEqual(out["due_at"], SEP8)

    def test_an_earlier_extension_is_the_starting_point(self):
        overrides = [{"id": 9, "student_ids": [7], "title": "Extension: Jane Doe",
                      "due_at": "2026-09-12T04:59:00Z"}]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertEqual(out["due_at"], "2026-09-12T04:59:00Z")
        self.assertEqual(out["override_id"], 9)
        self.assertEqual(out["shared_with"], 0)

    def test_the_most_lenient_of_several_wins(self):
        """Canvas's own rule, and the only one that cannot shorten a deadline."""
        overrides = [
            {"id": 5, "course_section_id": 100, "due_at": "2026-09-10T04:59:00Z"},
            {"id": 9, "student_ids": [7], "due_at": "2026-09-14T04:59:00Z"},
        ]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertEqual(out["due_at"], "2026-09-14T04:59:00Z")

    def test_an_override_with_no_due_date_beats_every_dated_one(self):
        overrides = [
            {"id": 5, "course_section_id": 100, "due_at": "2026-09-30T04:59:00Z"},
            {"id": 9, "student_ids": [7], "due_at": None},
        ]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertIsNone(out["due_at"])

    def test_a_shared_override_is_counted(self):
        overrides = [{"id": 9, "student_ids": [7, 8, 9], "due_at": SEP8}]
        out = extend.effective(target(), overrides, "7", ["100"])
        self.assertEqual(out["shared_with"], 2)


class WhatThePlanDecides(unittest.TestCase):
    def test_a_plain_row_moves_by_the_days_given(self):
        out = extend.plan([student()], [target()], 3)
        self.assertEqual(len(out["rows"]), 1)
        row = out["rows"][0]
        self.assertEqual(row["from_due"], SEP8)
        self.assertEqual(row["to_due"], "2026-09-12T04:59:00Z")
        self.assertEqual(row["action"], "create")

    def test_a_second_extension_updates_the_first_rather_than_colliding(self):
        """Canvas allows a student into one ad-hoc override per assignment, so
        a second create would be refused, not a longer deadline."""
        overrides = [{"id": 9, "student_ids": [7], "title": "Extension: Jane Doe",
                      "due_at": "2026-09-12T04:59:00Z"}]
        out = extend.plan([student()], [target(overrides=overrides)], 2)
        row = out["rows"][0]
        self.assertEqual(row["action"], "update")
        self.assertEqual(row["override_id"], 9)
        self.assertEqual(row["to_due"], "2026-09-14T04:59:00Z")

    def test_the_lock_date_moves_with_the_due_date(self):
        """An assignment that locks on the due date would otherwise refuse the
        work at the new deadline, making the extension a date that does
        nothing."""
        out = extend.plan([student()], [target(lock_at=SEP8)], 3)
        row = out["rows"][0]
        self.assertEqual(row["to_lock"], "2026-09-12T04:59:00Z")

    def test_a_lock_that_would_still_precede_the_deadline_is_pushed_out(self):
        out = extend.plan([student()], [target(lock_at="2026-09-09T00:00:00Z")], 3)
        row = out["rows"][0]
        self.assertGreaterEqual(extend.parse_iso(row["to_lock"]),
                                extend.parse_iso(row["to_due"]))

    def test_an_assignment_with_no_lock_gets_none(self):
        out = extend.plan([student()], [target()], 3)
        self.assertIsNone(out["rows"][0]["to_lock"])

    def test_somebody_not_in_the_course_is_skipped_quietly(self):
        out = extend.plan([student("999", "Other Person")], [target()], 3)
        self.assertEqual(out["rows"], [])
        self.assertTrue(out["skipped"][0]["quiet"])

    def test_no_due_date_means_nothing_to_extend(self):
        out = extend.plan([student()], [target(due_at=None)], 3)
        self.assertEqual(out["rows"], [])
        self.assertIn("nothing to extend", out["skipped"][0]["why"])

    def test_a_shared_override_is_left_alone_and_said_so(self):
        """Rewriting it would move the dates of students nobody selected."""
        overrides = [{"id": 9, "student_ids": [7, 8], "title": "Late group",
                      "due_at": SEP8}]
        out = extend.plan([student()], [target(overrides=overrides)], 3)
        self.assertEqual(out["rows"], [])
        skipped = out["skipped"][0]
        self.assertTrue(skipped["blocked"])
        self.assertIn("other student", skipped["why"])

    def test_submitted_work_is_skipped_but_shown(self):
        out = extend.plan([student()], [target()], 3, submitted={("10", "7")})
        self.assertEqual(out["rows"], [])
        self.assertTrue(out["skipped"][0]["submitted"])
        self.assertFalse(out["skipped"][0].get("quiet"))

    def test_submitted_work_can_be_included_on_request(self):
        out = extend.plan([student()], [target()], 3, submitted={("10", "7")},
                          include_submitted=True)
        self.assertEqual(len(out["rows"]), 1)
        self.assertTrue(out["rows"][0]["submitted"])

    def test_each_student_gets_their_own_row(self):
        people = [student("7", "Jane Doe"), student("8", "Sam Roe")]
        t = target(enrolled={"7", "8"}, sections={"7": ["100"], "8": ["100"]})
        out = extend.plan(people, [t], 3)
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual({r["user_id"] for r in out["rows"]}, {"7", "8"})

    def test_a_silly_number_of_days_is_refused(self):
        for days in (0, -3, extend.MAX_DAYS + 1):
            with self.assertRaises(ValueError):
                extend.plan([student()], [target()], days)

    def test_too_many_rows_at_once_is_refused(self):
        people = [student(str(i), f"Student {i}") for i in range(30)]
        ids = {str(i) for i in range(30)}
        targets = [target(assignment_id=str(a), enrolled=ids,
                          sections={i: ["100"] for i in ids})
                   for a in range(20)]
        with self.assertRaises(ValueError) as caught:
            extend.plan(people, targets, 3)
        self.assertIn("Narrow", str(caught.exception))


class TheSentenceSomebodyHasToAgreeTo(unittest.TestCase):
    def test_it_names_the_student_when_there_is_one(self):
        out = extend.plan([student()], [target()], 3)
        said = extend.describe(out, 3)
        self.assertIn("Jane Doe", said)
        self.assertIn("3 more days", said)
        self.assertIn("Nobody else", said)

    def test_it_counts_people_when_there_are_many(self):
        people = [student(str(i), f"Student {i}") for i in range(5)]
        ids = {str(i) for i in range(5)}
        t = target(enrolled=ids, sections={i: ["100"] for i in ids})
        said = extend.describe(extend.plan(people, [t], 1), 1)
        self.assertIn("5 students", said)
        self.assertIn("1 more day", said)

    def test_an_empty_plan_says_so_rather_than_nothing(self):
        self.assertIn("Nothing to move", extend.describe({"rows": []}, 3))

    def test_the_fingerprint_changes_when_a_row_is_unticked(self):
        """The confirmation token is bound to this, so agreeing to twelve
        changes cannot be spent on a thirteenth."""
        people = [student("7", "Jane Doe"), student("8", "Sam Roe")]
        t = target(enrolled={"7", "8"}, sections={"7": ["100"], "8": ["100"]})
        rows = extend.plan(people, [t], 3)["rows"]
        self.assertNotEqual(extend.batches(rows), extend.batches(rows[:1]))


class TheWindowLooksAtTheStudentsDate(unittest.TestCase):
    def test_a_section_override_inside_the_window_counts(self):
        """Class due is outside the absence; the section date is inside it."""
        overrides = [{"id": 5, "course_section_id": 100,
                      "due_at": "2026-09-11T04:59:00Z"}]
        a = target(due_at="2026-09-20T04:59:00Z", overrides=overrides)
        self.assertTrue(extend.touches_window(
            a, overrides, ["7"], {"7": ["100"]},
            "2026-09-10", "2026-09-12", TZ))
        self.assertFalse(extend.day_in_window(a["due_at"], "2026-09-10", "2026-09-12", TZ))

    def test_class_date_inside_the_window_still_counts(self):
        a = target()
        self.assertTrue(extend.touches_window(
            a, [], ["7"], {"7": ["100"]}, "2026-09-08", "2026-09-08", TZ))


class TheTitleItWritesOnCanvas(unittest.TestCase):
    def test_it_names_the_student(self):
        self.assertEqual(extend.title_for("Jane Doe", 7), "Extension: Jane Doe")

    def test_it_recognises_its_own_work_later(self):
        self.assertTrue(extend.is_ours(extend.title_for("Jane Doe", 7)))
        self.assertFalse(extend.is_ours("Late group"))
        self.assertFalse(extend.is_ours(""))

    def test_a_nameless_student_still_gets_a_usable_title(self):
        self.assertIn("7", extend.title_for("", 7))


if __name__ == "__main__":
    unittest.main()
