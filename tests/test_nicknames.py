"""Nicknames are a display spelling. The legal name stays the stored one."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from courseforge import identity, latepolicy, nicknames
from courseforge.store import Store
from courseforge.studentsarea import assemble


class _StoreApp:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.state_sync = None

    def taught_students(self, refresh=False, term=None):
        return {"count": 1, "students": [{
            "user_id": "7",
            "name": "Ada Lovelace",
            "sortable_name": "Lovelace, Ada",
            "sis_user_id": "S100",
            "login_id": "alovelace",
            "courses": ["Games"],
            "course_refs": [],
            "on_roster": False,
        }]}


class Spelling(unittest.TestCase):
    def test_john_doe_becomes_john_jack_doe(self):
        self.assertEqual(nicknames.format_name("John Doe", "Jack"),
                         'John "Jack" Doe')
        self.assertEqual(nicknames.format_name("John Michael Doe", '"Jack"'),
                         'John "Jack" Michael Doe')
        self.assertEqual(nicknames.format_name("Doe, John", "Jack"),
                         'John "Jack" Doe')
        self.assertEqual(nicknames.format_name("Madonna", "Mo"),
                         'Madonna "Mo"')
        self.assertEqual(nicknames.format_name("John Doe", ""), "John Doe")
        self.assertEqual(nicknames.format_name('John "Jack" Doe', "Jack"),
                         'John "Jack" Doe')

    def test_junk_is_refused_and_a_blank_clears(self):
        with self.assertRaises(ValueError):
            nicknames.clean("Jack2")
        with self.assertRaises(ValueError):
            nicknames.clean("x" * 41)
        self.assertEqual(nicknames.clean('  "Jack"  '), "Jack")
        self.assertEqual(nicknames.clean(""), "")
        self.assertEqual(nicknames.clean("O'Brien"), "O'Brien")


class BookRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        nicknames.bind(self.tmp)

    def tearDown(self):
        nicknames.bind(None)

    def test_newer_nickname_wins_per_student(self):
        merged = nicknames.merge_maps(
            {"1": {"nickname": "Jack", "updated": "2026-09-22T01:00:00+00:00"},
             "2": {"nickname": "Ann", "updated": "2026-09-22T01:00:00+00:00"}},
            {"1": {"nickname": "Johnny", "updated": "2026-09-22T02:00:00+00:00"},
             "2": {"nickname": "", "updated": "2026-09-20T00:00:00+00:00"}},
        )
        self.assertEqual(merged["1"]["nickname"], "Johnny")
        self.assertEqual(merged["2"]["nickname"], "Ann")

    def test_save_then_clear(self):
        app = _StoreApp(self.tmp)
        saved = nicknames.save(app, "42", "Jack")
        self.assertEqual(saved["nickname"], "Jack")
        self.assertEqual(nicknames.lookup("42"), "Jack")
        self.assertEqual(nicknames.shown("John Doe", "42"), 'John "Jack" Doe')
        nicknames.save(app, "42", "")
        self.assertEqual(nicknames.lookup("42"), "")
        self.assertEqual(nicknames.shown("John Doe", "42"), "John Doe")

    def test_the_list_keeps_the_legal_name_and_finds_the_nickname(self):
        app = _StoreApp(self.tmp)
        nicknames.save(app, "7", "Augusta")
        hit = assemble.search(app, "augusta")["students"]
        self.assertEqual([s["user_id"] for s in hit], ["7"])
        self.assertEqual(hit[0]["name"], "Ada Lovelace")
        self.assertEqual(hit[0]["display_name"], 'Ada "Augusta" Lovelace')
        self.assertEqual(hit[0]["sortable_name"], "Lovelace, Ada")


class SwappedBeforeItLeaves(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        nicknames.bind(self.tmp)
        nicknames.Book(self.tmp).write({
            "101": {"nickname": "Jack", "updated": "2026-09-22T00:00:00+00:00"},
        })
        self.nm = identity.NameMap("9", students=[{
            "id": 101, "name": "John Doe", "sortable_name": "Doe, John",
        }], path=self.tmp / "9" / "names.json")

    def tearDown(self):
        nicknames.bind(None)

    def test_the_nickname_and_the_quoted_form_become_the_tag(self):
        bare = self.nm.mask("Ask Jack about the quiz")
        self.assertNotIn("Jack", bare.text)
        self.assertIn("Student-1", bare.text)
        quoted = self.nm.mask('John "Jack" Doe turned it in')
        self.assertNotIn("Jack", quoted.text)
        self.assertEqual(self.nm.unmask(quoted.text),
                         'John "Jack" Doe turned it in')
        legal = self.nm.mask("John Doe turned it in")
        self.assertEqual(self.nm.unmask(legal.text),
                         'John "Jack" Doe turned it in')

    def test_names_json_keeps_the_canvas_name(self):
        self.nm.save()
        blob = (self.tmp / "9" / "names.json").read_text(encoding="utf-8")
        self.assertIn("John Doe", blob)
        self.assertNotIn("Jack", blob)
        self.assertNotIn("nickname", json.dumps(self.nm.to_json()))


class Waiver(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        nicknames.bind(self.tmp)
        nicknames.Book(self.tmp).write({
            "101": {"nickname": "Jack", "updated": "2026-09-22T00:00:00+00:00"},
        })
        self.student = {
            "user_id": "101", "name": "John Doe", "sortable_name": "Doe, John",
        }

    def tearDown(self):
        nicknames.bind(None)

    def test_either_spelling_lifts_the_dock(self):
        for text in (
            "Ignore Jack's tardy submission.",
            "Ignore John Doe's tardy submission.",
            'Ignore John "Jack" Doe\'s late work.',
            "Ignore Jack Doe's tardy submission.",
        ):
            self.assertTrue(latepolicy.waiver(text, self.student), text)
        self.assertEqual(
            latepolicy.waiver("Please look at Jack's formatting.", self.student),
            "")


class NotSentToTheModel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        nicknames.bind(self.tmp)
        nicknames.Book(self.tmp).write({
            "101": {"nickname": "Jack", "updated": "2026-09-22T00:00:00+00:00"},
        })

    def tearDown(self):
        nicknames.bind(None)

    def test_instructions_lose_the_nickname_when_names_are_tagged(self):
        class Fake:
            enabled = True
            identities = {"S-001": {"user_id": "101", "name": "John Doe"}}

        text = nicknames.redact("Ignore Jack's tardy submission.", Fake())
        self.assertNotIn("Jack", text)
        self.assertIn("S-001", text)
        self.assertEqual(
            nicknames.redact("Ignore Jack's tardy submission.",
                             type("Off", (), {"enabled": False, "identities": {}})()),
            "Ignore Jack's tardy submission.")


class PageUsesTheLabel(unittest.TestCase):
    def test_screens_show_the_label_and_the_save_keeps_the_legal_name(self):
        root = Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
        grade = (root / "grade.js").read_text(encoding="utf-8")
        student = (root / "student.js").read_text(encoding="utf-8")
        self.assertIn("esc(studentLabel(s))", grade)
        self.assertIn("name: s.name", grade)
        self.assertIn('id="stNick"', student)
        # A submission is the wrong place to type a nickname. The student
        # page and the accommodation roster are the two editors.
        self.assertNotIn('id="gdNick"', grade)
        roster = grade[grade.index("async function openRoster("):
                       grade.index("function utcToLocalInput(")]
        self.assertIn("data-nick=", roster)
        self.assertIn("saveNickname", roster)
        self.assertIn("delete copy.nickname", roster)
        self.assertIn("function sortKey(s)", grade)
        self.assertIn("s.name", grade[grade.index("function sortKey"):
                                       grade.index("function sortKey") + 120])
