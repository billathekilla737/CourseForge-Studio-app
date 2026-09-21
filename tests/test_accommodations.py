"""Standing accommodations roster: parse, drop-row count, merge by user_id."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from courseforge.accommodations import (
    MAX_ROSTER, Roster, Student, merge_students, parse_student,
)


class RosterLoadSave(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "accommodations.json"
        self.roster = Roster(self.path)

    def test_round_trip(self):
        s = parse_student({"user_id": "11", "name": "Ada", "kind": "percent",
                           "percent": 50})
        self.roster.save([s])
        got = self.roster.load()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].user_id, "11")
        self.assertEqual(got[0].percent, 50)

    def test_dropped_rows_are_counted(self):
        self.path.write_text(
            '{"students": [{"user_id": "11", "name": "Ada", "kind": "percent", "percent": 50},'
            ' {"user_id": "nope", "name": "Bad"}]}',
            encoding="utf-8")
        students, dropped = self.roster.load_report()
        self.assertEqual(len(students), 1)
        self.assertEqual(dropped, 1)

    def test_past_the_ceiling_is_refused(self):
        crowd = [parse_student({"user_id": str(i), "name": str(i),
                                "kind": "percent", "percent": 50})
                 for i in range(1, MAX_ROSTER + 2)]
        with self.assertRaises(ValueError):
            self.roster.save(crowd)


class RosterMerge(unittest.TestCase):
    def test_union_by_user_id(self):
        a = [parse_student({"user_id": "1", "name": "Ada", "percent": 50,
                            "updated": "2026-01-01T00:00:00"})]
        b = [parse_student({"user_id": "2", "name": "Bea", "percent": 100,
                            "updated": "2026-01-02T00:00:00"})]
        out, conflicts = merge_students(a, b)
        self.assertEqual({s.user_id for s in out}, {"1", "2"})
        self.assertEqual(conflicts, [])

    def test_newer_updated_wins_and_flags_a_disagreement(self):
        a = [parse_student({"user_id": "1", "name": "Ada", "percent": 50,
                            "updated": "2026-01-01T00:00:00"})]
        b = [parse_student({"user_id": "1", "name": "Ada", "percent": 100,
                            "updated": "2026-06-01T00:00:00"})]
        out, conflicts = merge_students(a, b)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].percent, 100)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["kept"]["percent"], 100)


if __name__ == "__main__":
    unittest.main()
