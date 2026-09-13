"""What the Assistant is allowed to know about a student.

The point of the module is one property: a real name cannot come out of it. So
most of what is here is an attempt to get a name out, through the fields where
one plausibly hides -- the roster, a comment somebody typed, a flag, the
record.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from courseforge import identity, students

ROSTER = [
    {"id": 101, "name": "Jordan Alvarez", "sortable_name": "Alvarez, Jordan",
     "login_id": "jalvarez3", "sis_user_id": "A00412233",
     "email": "jalvarez3@example.edu"},
    {"id": 205, "name": "Dana Wu", "sortable_name": "Wu, Dana"},
]


class FakeStudent:
    def __init__(self, uid, label):
        self.user_id = uid
        self._label = label

    def label(self):
        return self._label


class FakeRoster:
    def __init__(self, rows):
        self.rows = rows

    def load(self):
        return self.rows


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)

    def assignments(self, cid):
        return [{"id": "900", "name": "Project 2: Modeling"}]

    def draft(self, cid, aid):
        path = self.root / str(cid) / str(aid) / "draft.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


class FakeApp:
    def __init__(self, root, accommodations=()):
        self.root = Path(root)
        self.store = FakeStore(root)
        self.roster = FakeRoster(list(accommodations))
        self.cfg = type("C", (), {"data": str(root), "pseudonymize": True})()
        self.client = type("C", (), {"students": staticmethod(lambda cid: ROSTER)})()

    def course_dir(self, cid):
        p = self.root / str(cid)
        p.mkdir(parents=True, exist_ok=True)
        return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        identity.forget()
        self.addCleanup(identity.forget)
        self.app = FakeApp(self.tmp)

    def draft(self, **entry):
        adir = self.tmp / "77" / "900"
        adir.mkdir(parents=True, exist_ok=True)
        base = {"user_id": "101", "total": 41.0, "source": "claude",
                "comment": "", "rationales": {}, "flags": [],
                "needs_human": False, "needs_human_reason": ""}
        base.update(entry)
        (adir / "draft.json").write_text(json.dumps({
            "assignment_id": "900", "assignment_name": "Project 2: Modeling",
            "points_possible": 50, "students": {base["user_id"]: base}}),
            encoding="utf-8")


class NoNameGetsOut(Base):
    """Every field that could carry one, tried one at a time."""

    def test_the_answer_is_keyed_by_tag(self):
        self.draft()
        out = students.student_view(self.app, 77, "Student-1")
        self.assertEqual(out["tag"], "Student-1")
        self.assertNotIn("user_id", json.dumps(out))
        self.assertEqual(out["average_percent"], 82.0)

    def test_a_name_typed_into_a_comment_is_scrubbed(self):
        self.draft(comment="Jordan Alvarez needs to apply scale before exporting.")
        out = students.student_view(self.app, 77, "Student-1")
        self.assertNotIn("Jordan", json.dumps(out))
        self.assertNotIn("Alvarez", json.dumps(out))

    def test_an_email_or_a_login_in_free_text_is_scrubbed(self):
        self.draft(needs_human_reason="emailed jalvarez3@example.edu about this",
                   flags=["sis A00412233 mismatch"])
        blob = json.dumps(students.student_view(self.app, 77, "Student-1"))
        for secret in ("jalvarez3", "example.edu", "A00412233"):
            self.assertNotIn(secret, blob, secret)

    def test_a_name_in_a_rationale_is_scrubbed(self):
        self.draft(rationales={"Topology": "Jordan Alvarez left n-gons in the mesh."})
        self.assertNotIn("Alvarez", json.dumps(students.student_view(self.app, 77, "Student-1")))

    def test_the_class_view_names_nobody(self):
        self.draft()
        blob = json.dumps(students.class_view(self.app, 77))
        for name in ("Jordan", "Alvarez", "Dana", "Wu"):
            self.assertNotIn(name, blob, name)

    def test_the_last_gate_would_actually_fire(self):
        """assert_clean is the backstop, so it has to be able to fail."""
        with self.assertRaises(RuntimeError):
            students.assert_clean({"note": "ask Jordan Alvarez"}, ["Jordan Alvarez"])


class WhatItSays(Base):
    def test_a_flagged_student_says_so_and_why(self):
        self.draft(needs_human=True, needs_human_reason="rubric row is ambiguous")
        out = students.student_view(self.app, 77, "Student-1")
        self.assertEqual(out["needs_a_person"], 1)
        self.assertEqual(out["work"][0]["why"], "rubric row is ambiguous")

    def test_an_accommodation_on_record_is_carried(self):
        app = FakeApp(self.tmp, accommodations=[FakeStudent("101", "time and a half")])
        self.draft()
        out = students.student_view(app, 77, "Student-1")
        self.assertEqual(out["accommodation"], "time and a half")

    def test_nothing_graded_is_said_plainly_not_left_blank(self):
        """"Nothing graded here" and "submitted nothing" are different, and the
        difference is the whole of what this tool can honestly claim."""
        out = students.student_view(self.app, 77, "Student-2")
        self.assertEqual(out["work"], [])
        self.assertIn("does not mean they have submitted nothing", out["summary"])

    def test_a_tag_nobody_owns_says_what_the_tags_run_to(self):
        with self.assertRaises(students.NotOnThisRoster) as caught:
            students.student_view(self.app, 77, "Student-999")
        self.assertIn("Student-1 to Student-2", str(caught.exception))

    def test_it_makes_no_canvas_call_beyond_the_roster_it_already_cached(self):
        calls = []
        self.draft()
        with mock.patch.object(self.app.client, "students",
                               staticmethod(lambda cid: calls.append(cid) or ROSTER)):
            students.student_view(self.app, 77, "Student-1")
            students.student_view(self.app, 77, "Student-1")
        self.assertLessEqual(len(calls), 1, "it re-read the roster per question")


class TheGateKnowsTheVerbs(unittest.TestCase):
    def test_the_reads_run_without_an_allow_click(self):
        from courseforge.assistant import gate
        self.assertEqual(gate.STUDIO_VERBS["students"], {"list", "show"})
        for verb in ("list", "show"):
            problem, _kind, *_ = gate._studio_problem(
                ["students", verb, "--course", "77"], "")
            self.assertIsNone(problem, verb)

    def test_a_verb_it_does_not_have_is_still_refused(self):
        from courseforge.assistant import gate
        problem, _kind, *_ = gate._studio_problem(["students", "grade", "--course", "77"], "")
        self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main(verbosity=2)
