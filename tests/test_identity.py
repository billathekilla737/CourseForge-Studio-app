"""Real names out, tags in: what the Assistant sends and what it refuses to.

The whole point of the module is that a real name never reaches Anthropic, so
most of these tests are the same assertion from different angles -- that a
particular way of writing a student's name does not survive `mask`.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import identity

ROSTER = [
    {"id": 101, "name": "Jordan Alvarez", "sortable_name": "Alvarez, Jordan",
     "short_name": "Jordan", "login_id": "jalvarez3", "sis_user_id": "A00412233",
     "email": "jalvarez3@example.edu"},
    {"id": 205, "name": "Dana Wu", "sortable_name": "Wu, Dana", "short_name": "Dana"},
    {"id": 307, "name": "Chris Okafor", "sortable_name": "Okafor, Chris"},
    {"id": 404, "name": "Robin Okafor", "sortable_name": "Okafor, Robin"},
    {"id": 512, "name": "Casey Long", "sortable_name": "Long, Casey"},
]


class Tags(unittest.TestCase):
    def setUp(self):
        self.nm = identity.NameMap(734975, ROSTER)

    def test_tags_are_handed_out_in_canvas_id_order(self):
        self.assertEqual(self.nm.tag_for(101), "Student-1")
        self.assertEqual(self.nm.tag_for(512), "Student-5")

    def test_a_new_student_gets_a_new_tag_and_nobody_else_moves(self):
        """A tag that changed meaning between two sessions would make a saved
        conversation say something untrue about a real person."""
        before = dict(self.nm.by_user)
        self.assertTrue(self.nm.absorb([{"id": 99, "name": "Ada Early",
                                         "sortable_name": "Early, Ada"}]))
        for uid, tag in before.items():
            self.assertEqual(self.nm.tag_for(uid), tag, "a tag moved under a student")
        self.assertEqual(self.nm.tag_for(99), "Student-6")

    def test_absorbing_the_same_roster_twice_changes_nothing(self):
        self.assertFalse(self.nm.absorb(ROSTER))
        self.assertEqual(len(self.nm), len(ROSTER))


class Masking(unittest.TestCase):
    def setUp(self):
        self.nm = identity.NameMap(734975, ROSTER)

    def out(self, text):
        return self.nm.mask(text).text

    def test_a_full_name_goes_as_a_tag(self):
        self.assertEqual(self.out("Why is Jordan Alvarez failing?"),
                         "Why is Student-1 failing?")

    def test_a_name_at_the_end_of_a_sentence(self):
        """The commonest way anyone writes a name. An earlier word boundary
        stopped at a full stop and left every one of these untouched."""
        for text in ("I need to email Dana Wu.", "Ask Dana Wu, please.",
                     "(Dana Wu)", "Dana Wu's essay", "-- Dana Wu"):
            self.assertNotIn("Dana Wu", self.out(text), text)

    def test_case_does_not_matter_for_a_full_name(self):
        self.assertEqual(self.out("jordan alvarez missed the quiz"),
                         "Student-1 missed the quiz")

    def test_a_first_name_on_its_own_when_only_one_student_has_it(self):
        self.assertEqual(self.out("Is Dana caught up?"), "Is Student-2 caught up?")

    def test_the_login_the_sis_id_and_the_email_are_names_too(self):
        for value in ("jalvarez3", "A00412233", "jalvarez3@example.edu"):
            self.assertNotIn(value, self.out("look up " + value), value)

    def test_a_surname_written_last_name_first(self):
        self.assertEqual(self.out("Alvarez, Jordan is behind"), "Student-1 is behind")

    def test_an_ordinary_word_that_is_also_a_surname_is_left_alone(self):
        """Casey Long is on the roster. Swapping the bare word would rewrite
        "how long is the essay" into nonsense, which is worse than not
        swapping: no real name is exposed by leaving an English word alone."""
        self.assertEqual(self.out("how long is the essay?"), "how long is the essay?")
        self.assertEqual(self.out("Casey Long is behind"), "Student-5 is behind")

    def test_a_shared_surname_is_refused_rather_than_guessed(self):
        """Two Okafors. Sending it leaks a real surname; picking one answers
        about the wrong student."""
        m = self.nm.mask("Has Okafor turned anything in?")
        self.assertTrue(m.ambiguous)
        self.assertIn("Chris Okafor", m.sentence())
        self.assertIn("Robin Okafor", m.sentence())
        self.assertIn("full name", m.sentence())

    def test_but_the_full_name_of_a_shared_surname_is_fine(self):
        m = self.nm.mask("Has Robin Okafor turned anything in?")
        self.assertEqual(m.ambiguous, [])
        self.assertEqual(m.text, "Has Student-4 turned anything in?")

    def test_several_names_in_one_message(self):
        m = self.nm.mask("Compare Jordan Alvarez with Dana Wu.")
        self.assertEqual(m.text, "Compare Student-1 with Student-2.")
        self.assertEqual({s.tag for s in m.swaps}, {"Student-1", "Student-2"})

    def test_it_reports_what_it_swapped_so_the_page_can_say_so(self):
        m = self.nm.mask("Jordan Alvarez again")
        self.assertEqual(m.swaps[0].view(),
                         {"wrote": "Jordan Alvarez", "tag": "Student-1",
                          "name": "Jordan Alvarez"})

    def test_turning_it_off_sends_what_was_typed(self):
        off = identity.NameMap(734975, ROSTER, enabled=False)
        self.assertEqual(off.mask("Jordan Alvarez").text, "Jordan Alvarez")

    def test_an_empty_roster_masks_nothing_rather_than_failing(self):
        empty = identity.NameMap(734975, [])
        self.assertEqual(empty.mask("Jordan Alvarez").text, "Jordan Alvarez")
        self.assertIn("No roster", empty.roster_note())


class Unmasking(unittest.TestCase):
    def setUp(self):
        self.nm = identity.NameMap(734975, ROSTER)

    def test_tags_come_back_as_names_for_the_page(self):
        self.assertEqual(self.nm.unmask("Student-2 has three missing pieces."),
                         "Dana Wu has three missing pieces.")

    def test_a_tag_nobody_owns_is_left_as_it_is(self):
        self.assertEqual(self.nm.unmask("Student-99 said so"), "Student-99 said so")

    def test_a_round_trip_gives_back_what_was_typed(self):
        typed = "Did Jordan Alvarez and Dana Wu both submit?"
        self.assertEqual(self.nm.unmask(self.nm.mask(typed).text), typed)

    def test_every_readable_field_of_an_event_is_turned_back(self):
        ev = {"kind": "tool", "summary": "reading Student-1's page",
              "text": "Student-2 is fine", "seq": 4}
        out = self.nm.unmask_event(ev)
        self.assertEqual(out["summary"], "reading Jordan Alvarez's page")
        self.assertEqual(out["text"], "Dana Wu is fine")
        self.assertEqual(out["seq"], 4)
        self.assertEqual(ev["text"], "Student-2 is fine", "it edited the stored event")


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / identity.FILE

    def test_it_survives_a_restart(self):
        identity.NameMap(1, ROSTER, path=self.path).save()
        back = identity.NameMap(1, path=self.path)
        self.assertTrue(back.load())
        self.assertEqual(back.tag_for(101), "Student-1")
        self.assertEqual(back.mask("Dana Wu").text, "Student-2")

    def test_the_file_says_it_must_not_leave_the_machine(self):
        """It is the only file in the tree that turns a tag back into a person.
        Whoever opens it next should not have to guess what it is."""
        identity.NameMap(1, ROSTER, path=self.path).save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("never uploaded", raw["note"])
        self.assertIn("Anthropic", raw["note"])

    def test_a_corrupt_file_is_not_a_crash(self):
        self.path.write_text("{ not json", encoding="utf-8")
        nm = identity.NameMap(1, path=self.path)
        self.assertFalse(nm.load())
        self.assertEqual(len(nm), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
