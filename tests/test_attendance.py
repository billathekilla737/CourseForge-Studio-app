"""Attendance: class days, tardies, absences, and what is stored.

The book is user ids and marks. A name on the screen comes from the class
list at read time and is not written into the file that syncs.
"""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from courseforge.attendance import book


class Book(unittest.TestCase):
    def test_mondays_skip_a_break_unless_that_day_is_put_back(self):
        stored = book.set_pattern(
            book.empty_book(), ["mon"], "2026-09-01", "2026-09-14", True)
        days = [d.isoformat() for d in book.meeting_dates(stored, {date(2026, 9, 7)})]
        self.assertEqual(days, ["2026-09-14"])
        stored = book.set_meet(stored, "2026-09-07", True)
        days = [d.isoformat() for d in book.meeting_dates(stored, {date(2026, 9, 7)})]
        self.assertEqual(days, ["2026-09-07", "2026-09-14"])

    def test_a_cancelled_day_drops_out_and_its_marks_do_not_count(self):
        stored = book.set_pattern(
            book.empty_book(), ["mon"], "2026-09-07", "2026-09-14", False)
        stored = book.apply_marks(stored, "2026-09-07", [
            {"user_id": "101", "status": "absent"},
        ])
        stored = book.set_meet(stored, "2026-09-07", False)
        meetings = book.meeting_dates(stored, set())
        self.assertNotIn(date(2026, 9, 7), meetings)
        rows = {r["user_id"]: r for r in book.totals(stored, meetings, ["101"])}
        self.assertEqual(rows["101"]["absent"], 0)

    def test_tardy_minutes_count_and_a_clear_drops_off_the_screen(self):
        stored = book.set_pattern(
            book.empty_book(), ["wed"], "2026-09-02", "2026-09-02", False)
        stored = book.apply_marks(stored, "2026-09-02", [
            {"user_id": "101", "status": "tardy", "minutes": 12},
            {"user_id": "205", "status": "absent"},
        ])
        meetings = book.meeting_dates(stored, set())
        rows = {r["user_id"]: r for r in book.totals(stored, meetings, ["101", "205"])}
        self.assertEqual(rows["101"]["tardy"], 1)
        self.assertEqual(rows["101"]["minutes"], 12)
        self.assertEqual(rows["101"]["absent"], 0)
        self.assertEqual(rows["205"]["absent"], 1)
        stored = book.apply_marks(stored, "2026-09-02", [
            {"user_id": "205", "status": "clear"},
        ])
        shown = book.public_marks(stored).get("2026-09-02") or {}
        self.assertNotIn("205", shown)
        self.assertEqual(shown["101"]["minutes"], 12)

    def test_marking_the_rest_present_does_not_overwrite_an_absence(self):
        stored = book.apply_marks(book.empty_book(), "2026-09-02", [
            {"user_id": "205", "status": "absent"},
        ])
        stored = book.apply_marks(
            stored, "2026-09-02", [], roster_ids=["101", "205"], fill="present")
        cell = book.public_marks(stored)["2026-09-02"]
        self.assertEqual(cell["101"]["status"], "present")
        self.assertEqual(cell["205"]["status"], "absent")

    def test_a_later_clear_wins_the_merge(self):
        day = "2026-09-02"
        left = {"marks": {day: {"101": {"status": "absent", "minutes": 0,
                                        "note": "", "updated": "2026-09-02T12:00:00+00:00"}}}}
        right = {"marks": {day: {"101": {"status": "", "minutes": 0,
                                         "note": "", "updated": "2026-09-03T12:00:00+00:00"}}}}
        merged = book.merge_books(left, right)
        self.assertEqual(merged["marks"][day]["101"]["status"], "")
        self.assertNotIn("101", book.public_marks(merged).get(day) or {})

    def test_the_newer_pattern_wins_and_no_name_is_stored(self):
        older = book.set_pattern(book.empty_book(), ["mon"], "2026-09-01", "2026-09-30", True)
        newer = book.set_pattern(book.empty_book(), ["wed"], "2026-09-01", "2026-09-30", True)
        older["pattern_updated"] = "2026-09-01T00:00:00+00:00"
        newer["pattern_updated"] = "2026-09-02T00:00:00+00:00"
        merged = book.merge_books(older, newer)
        self.assertEqual(merged["weekdays"], ["wed"])
        self.assertNotIn("name", json.dumps(merged))

    def test_a_bad_mark_is_refused(self):
        with self.assertRaises(ValueError):
            book.apply_marks(book.empty_book(), "2026-09-02", [
                {"user_id": "101", "status": "late"},
            ])
        with self.assertRaises(ValueError):
            book.set_pattern(book.empty_book(), [], "2026-09-02", "2026-09-01", True)


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = type("App", (), {})()
        self.app.store = type("Store", (), {})()
        self.app.store.root = self.tmp
        self.app.store.courses = lambda: [{
            "id": "734975", "title": "Intro", "term_label": "Fall 2026",
        }]
        self.app.client = None
        self.app.state_sync = None

        def course_dir(cid):
            path = self.tmp / str(cid)
            path.mkdir(parents=True, exist_ok=True)
            return path

        self.app.course_dir = course_dir
        (self.tmp / "734975").mkdir()
        (self.tmp / "734975" / "students.json").write_text(json.dumps([
            {"id": 101, "name": "Casey Quinn", "sortable_name": "Quinn, Casey"},
        ]), encoding="utf-8")

    def test_the_file_has_the_id_and_not_the_name(self):
        stored = book.apply_marks(book.empty_book(), "2026-09-02", [
            {"user_id": "101", "status": "tardy", "minutes": 5, "note": "bus"},
        ])
        book.save(self.app, "734975", stored)
        raw = (self.tmp / "734975" / "attendance.json").read_text(encoding="utf-8")
        self.assertIn("101", raw)
        self.assertNotIn("Casey", raw)
        self.assertNotIn("Quinn", raw)
        shown = book.view(self.app, "734975")
        self.assertEqual(shown["roster"][0]["name"], "Casey Quinn")
        self.assertEqual(shown["marks"]["2026-09-02"]["101"]["status"], "tardy")
        self.assertIn("gradebook is not changed", shown["note"])

    def test_opening_the_page_reads_disk_and_does_not_call_canvas(self):
        from courseforge.attendance import routes
        from unittest import mock
        book.save(self.app, "734975", book.empty_book())
        req = type("R", (), {"app": self.app, "params": {"cid": "734975"}})()
        with mock.patch.object(book, "reconcile", side_effect=AssertionError("sync")):
            out = routes.show(req)
        self.assertEqual(out["course_id"], "734975")
        self.assertFalse(out["pulled"])

    def test_the_first_open_pulls_the_canvas_copy(self):
        from courseforge.attendance import routes
        from unittest import mock
        stored = book.apply_marks(book.empty_book(), "2026-09-02", [
            {"user_id": "101", "status": "absent"},
        ])
        req = type("R", (), {"app": self.app, "params": {"cid": "734975"}})()
        with mock.patch.object(book, "reconcile", return_value=stored) as pulled:
            out = routes.show(req)
        pulled.assert_called_once()
        self.assertTrue(out["pulled"])
        self.assertEqual(out["marks"]["2026-09-02"]["101"]["status"], "absent")

    def test_a_book_canvas_has_not_confirmed_is_outstanding(self):
        stored = book.apply_marks(book.empty_book(), "2026-09-02", [
            {"user_id": "101", "status": "tardy", "minutes": 5},
        ])
        book.save(self.app, "734975", stored)
        self.assertEqual(book.outstanding(self.app), ["734975"])
        from courseforge.statesync import _digest, save_sidecar
        save_sidecar(self.tmp, book.sync_key("734975"),
                     {"sha256": _digest(book.clean_book(stored))})
        self.assertEqual(book.outstanding(self.app), [])

    def test_opening_does_not_need_canvas_when_nothing_is_synced(self):
        shown = book.view(self.app, "734975", book.reconcile(self.app, "734975"))
        self.assertEqual(shown["roster"][0]["user_id"], "101")
        self.assertEqual(shown["pattern"]["weekdays"], [])


class TheScreenIsReachable(unittest.TestCase):
    def test_the_course_page_links_to_the_calendar(self):
        root = Path(__file__).resolve().parent.parent
        html = (root / "courseforge" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("js/attendance.js", html)
        hub = (root / "courseforge" / "web" / "js" / "hub.js").read_text(encoding="utf-8")
        self.assertIn("#/c/${cid}/attendance", hub)
        src = (root / "courseforge" / "web" / "js" / "attendance.js").read_text(encoding="utf-8")
        self.assertIn("registerArea", src)
        self.assertIn("id: 'attendance'", src)
        self.assertNotIn("location.reload", src)
        self.assertIn("pagehide", src)
        self.assertIn("keepalive: true", src)
        self.assertIn("onLeave", src)
        self.assertIn("AUTOSAVE_MS = 4000", src)
        self.assertIn("/flush", src)
        self.assertIn("function glanceName(", src)
        self.assertIn("class=\"btn sm atMark", src)
        self.assertIn("atWhoLine", src)
        css = (root / "courseforge" / "web" / "css" / "attendance.css").read_text(encoding="utf-8")
        self.assertIn(".atMark.present[aria-pressed=\"true\"]", css)
        self.assertIn(".atMark.tardy[aria-pressed=\"true\"]", css)
        self.assertIn(".atMark.absent:hover", css)
        self.assertIn(".atMark.excused[aria-pressed=\"true\"]", css)
        self.assertIn(".atWhoLine.absent", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
