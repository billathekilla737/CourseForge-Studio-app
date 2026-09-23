"""The 'who needs a look' score, and a scan that stays on this term."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from courseforge import terms
from courseforge.store import Store
from courseforge.studentsarea import risk


class ScoreCourse(unittest.TestCase):
    def test_nothing_due_is_not_a_risk(self):
        score, reasons = risk.score_course({
            "grade": None, "missing": 0, "zeros": 0,
            "days_since_submit": None, "days_since_activity": 1,
        })
        self.assertEqual(score, 0)
        self.assertEqual(reasons, [])

    def test_a_failing_grade_and_missing_work_add(self):
        score, reasons = risk.score_course({
            "grade": 55, "missing": 2, "zeros": 1,
            "days_since_submit": 20, "days_since_activity": 20,
        })
        self.assertGreaterEqual(score, 50)
        self.assertTrue(any("55" in r for r in reasons))
        self.assertTrue(any("past-due" in r for r in reasons))
        self.assertEqual(risk.band(score), "high")


class AttendanceIsItsOwnScore(unittest.TestCase):
    def test_absences_carry_it_and_tardies_add_a_little(self):
        two, reasons = risk.score_attendance(2, 0)
        self.assertEqual(two, 24)
        self.assertEqual(risk.band(two), "low")
        self.assertIn("2 absences", reasons)
        nudged, more = risk.score_attendance(2, 4)
        self.assertEqual(nudged, 32)
        self.assertEqual(risk.band(nudged), "medium")
        self.assertTrue(any("tard" in r for r in more))
        light, _ = risk.score_attendance(0, 5)
        self.assertLess(light, 25)
        self.assertEqual(risk.score_attendance(0, 0), (0, []))
        capped, _ = risk.score_attendance(8, 20)
        self.assertEqual(capped, 88)

    def test_a_marked_absence_shows_up_beside_the_work_score(self):
        label = terms.label_for(terms.current_code())
        code = terms.current_code()
        root = Path(tempfile.mkdtemp())
        course = root / "9"
        course.mkdir()
        (course / "attendance.json").write_text(
            '{"schema":1,"weekdays":["mon"],"start":"2020-01-06","end":"2020-01-06",'
            '"skip_breaks":false,"meet":{},"marks":{"2020-01-06":{"7":'
            '{"status":"absent","minutes":0,"note":"","updated":"2020-01-07T00:00:00+00:00"},'
            '"8":{"status":"tardy","minutes":5,"note":"","updated":"2020-01-07T00:00:00+00:00"},'
            '"9":{"status":"excused","minutes":0,"note":"","updated":"2020-01-07T00:00:00+00:00"}}}}',
            encoding="utf-8")

        class Client:
            def paged(self, path, **params):
                if path.endswith("/assignments"):
                    return [{"id": 1, "published": True,
                             "due_at": "2020-01-01T00:00:00Z", "points_possible": 10}]
                if path.endswith("/students/submissions"):
                    return [{"user_id": uid, "assignment_id": 1,
                             "workflow_state": "graded", "submitted_at": "2020-01-01T00:00:00Z",
                             "score": 9} for uid in (7, 8, 9)]
                if path.endswith("/enrollments"):
                    return [{"type": "StudentEnrollment", "user_id": uid,
                             "user": {"name": "Student"},
                             "grades": {"current_score": 90},
                             "last_activity_at": "2026-09-20T00:00:00Z"}
                            for uid in (7, 8, 9)]
                raise AssertionError(path)

        class App:
            def __init__(self):
                self.store = Store(root)
                self.client = Client()

            def courses(self, refresh=False):
                return [{"id": "9", "name": "Games now", "course_code": "Games",
                         "term_label": label, "term_code": code,
                         "term_sort": int(code) if str(code).isdigit() else 0,
                         "excluded": False}]

        risk.scan(App(), lambda *_a, **_k: None)
        saved = {row["user_id"]: row for row in risk.load(App()).get("students") or []}
        self.assertEqual(saved["7"]["attend"], 12)
        self.assertEqual(saved["7"]["score"] < 25, True)
        self.assertEqual(saved["8"]["attend"], 2)
        self.assertEqual(saved["9"]["attend"], 0)
        self.assertTrue(any("absence" in r for r in saved["7"]["attend_reasons"]))
        shown = risk.refresh_attendance(App(), {"students": [{
            "user_id": "7", "score": 10, "band": "low",
            "courses": [{"id": "9", "name": "Games", "score": 10, "band": "low"}],
        }], "counts": {"high": 0, "medium": 0, "low": 1}, "history": []})
        self.assertEqual(shown["students"][0]["attend"], 12)
        self.assertEqual(shown["students"][0]["score"], 10)


class ScanThisTermOnly(unittest.TestCase):
    def test_a_past_course_is_not_read_and_the_second_scan_has_a_trend(self):
        label = terms.label_for(terms.current_code())
        code = terms.current_code()
        root = Path(tempfile.mkdtemp())
        seen = []

        class Client:
            def paged(self, path, **params):
                seen.append(path)
                if path.endswith("/assignments"):
                    return [{
                        "id": 1, "published": True,
                        "due_at": "2020-01-01T00:00:00Z", "points_possible": 10,
                    }]
                if path.endswith("/students/submissions"):
                    return [{
                        "user_id": 7, "assignment_id": 1,
                        "workflow_state": "unsubmitted", "missing": True,
                        "submitted_at": None, "score": None,
                    }]
                if path.endswith("/enrollments"):
                    return [{
                        "type": "StudentEnrollment", "user_id": 7,
                        "user": {"name": "Ada Lovelace"},
                        "grades": {"current_score": 58},
                        "last_activity_at": "2020-01-02T00:00:00Z",
                    }]
                raise AssertionError(path)

        class App:
            def __init__(self):
                self.store = Store(root)
                self.client = Client()

            def courses(self, refresh=False):
                return [
                    {"id": "9", "name": "Games now", "course_code": "Games",
                     "term_label": label, "term_code": code,
                     "term_sort": int(code) if str(code).isdigit() else 0,
                     "excluded": False},
                    {"id": "3", "name": "Old games", "term_label": "Fall 1999",
                     "term_code": "199930", "term_sort": 199930, "excluded": False},
                ]

        app = App()
        risk.scan(app, lambda *_a, **_k: None)
        paths = [p for p in seen if "/3/" in p or p.startswith("/courses/3")]
        self.assertEqual(paths, [])
        saved = risk.load(app)
        self.assertEqual(saved["courses_checked"], 1)
        self.assertTrue(saved.get("scanned_at"))
        self.assertGreaterEqual(saved["students"][0]["score"], 25)
        self.assertIsNone(saved["students"][0]["trend"])
        self.assertEqual(saved["ledger"]["9"]["assignments"], ["1"])
        first = saved["students"][0]["score"]
        submission_reads = [p for p in seen if p.endswith("/students/submissions")]
        self.assertEqual(len(submission_reads), 1)

        risk.scan(app, lambda *_a, **_k: None)
        again = risk.load(app)
        self.assertEqual(again["students"][0]["trend"], 0)
        self.assertEqual(again["students"][0]["score"], first)
        self.assertEqual(len(again["history"]), 1)
        self.assertEqual(again["new_due"], 0)
        submission_reads = [p for p in seen if p.endswith("/students/submissions")]
        self.assertEqual(len(submission_reads), 1,
                         "a second check must not re-read a scanned assignment")


class CursorFromCanvas(unittest.TestCase):
    def test_a_newer_canvas_date_is_what_the_next_scan_uses(self):
        root = Path(tempfile.mkdtemp())

        class Sync:
            def get(self, key):
                return {"payload": {
                    "scanned_at": "2099-01-01T00:00:00+00:00",
                    "ledger": {"9": {"assignments": ["1"], "students": {}}},
                    "students": [],
                }}

        class App:
            def __init__(self):
                self.store = Store(root)
                self.state_sync = Sync()

        got = risk.load_for_use(App())
        self.assertEqual(got["scanned_at"], "2099-01-01T00:00:00+00:00")
        self.assertEqual(got["ledger"]["9"]["assignments"], ["1"])


if __name__ == "__main__":
    unittest.main()
