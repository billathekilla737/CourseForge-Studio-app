"""The account of what was done: the chain, and what breaking it looks like.

The point of these is the unhappy case. A record that is only checked when it
is intact proves nothing, so most of what is here is: change the file the way
somebody would, and make sure `verify` says so, at the right entry, for the
right reason.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from courseforge import audit


class Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audit.forget_actor()
        audit.set_actor_source(lambda: {"id": 7, "name": "Z. Garris"},
                               {"name": "STAFF-PC"})
        self.addCleanup(audit.set_actor_source, lambda: {}, {})
        self.addCleanup(audit.forget_actor)

    def write(self, n=3):
        for i in range(n):
            audit.record(self.tmp, "accommodations", "applied",
                         f"Set accommodations on quiz {i}.",
                         students=[audit.person(900 + i, f"Student {i}", extra_time=30)],
                         count=1, course_id="734975")

    def file(self):
        return audit.months(self.tmp)[0]

    def rows(self):
        return [json.loads(l) for l in
                self.file().read_text(encoding="utf-8").splitlines() if l.strip()]

    # ---------------------------------------------------------- the happy bit
    def test_entries_are_numbered_and_linked(self):
        self.write(3)
        rows = self.rows()
        self.assertEqual([r["seq"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["prev"], audit.GENESIS)
        self.assertEqual(rows[1]["prev"], rows[0]["hash"])
        self.assertEqual(rows[2]["prev"], rows[1]["hash"])
        self.assertTrue(audit.verify(self.tmp)["ok"])

    def test_it_says_whose_account_and_which_machine(self):
        self.write(1)
        row = self.rows()[0]
        self.assertEqual(row["actor"], {"id": 7, "name": "Z. Garris"})
        self.assertEqual(row["machine"], "STAFF-PC")

    def test_an_accommodation_names_the_student(self):
        """The whole reason this file exists. A record of accommodations that
        does not say who they were for answers nothing."""
        self.write(1)
        person = self.rows()[0]["students"][0]
        self.assertEqual(person["id"], "900")
        self.assertEqual(person["name"], "Student 0")
        self.assertEqual(person["extra_time"], 30)

    def test_it_survives_the_chain_file_being_lost(self):
        """Rebuilt from the entries themselves rather than starting a second
        chain beside the first, which would look exactly like tampering."""
        self.write(2)
        (audit.folder(self.tmp) / audit.CHAIN).unlink()
        audit.record(self.tmp, "grade", "posted", "Wrote 4 grades.")
        self.assertTrue(audit.verify(self.tmp)["ok"], audit.verify(self.tmp))
        self.assertEqual(self.rows()[-1]["seq"], 3)

    # ------------------------------------------------------- the unhappy bits
    def test_editing_an_entry_is_caught_and_named(self):
        self.write(3)
        rows = self.rows()
        rows[1]["sentence"] = "Set accommodations on quiz 1, honestly."
        self.file().write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                               encoding="utf-8")
        out = audit.verify(self.tmp)
        self.assertFalse(out["ok"])
        self.assertEqual(out["broke_at"]["seq"], 2)
        self.assertIn("text was changed", out["why"])

    def test_deleting_an_entry_is_caught(self):
        self.write(3)
        rows = self.rows()
        del rows[1]
        self.file().write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                               encoding="utf-8")
        out = audit.verify(self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("removed or inserted", out["why"])

    def test_slipping_an_entry_in_afterwards_is_caught(self):
        """The forgery that matters: a line saying an accommodation was given,
        added after somebody complained that it was not."""
        self.write(2)
        rows = self.rows()
        forged = dict(rows[1], seq=2, sentence="Set accommodations for everyone.")
        rows.insert(1, forged)
        self.file().write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                               encoding="utf-8")
        self.assertFalse(audit.verify(self.tmp)["ok"])

    def test_an_empty_record_is_not_a_broken_one(self):
        out = audit.verify(self.tmp)
        self.assertTrue(out["ok"])
        self.assertEqual(out["entries"], 0)
        self.assertIn("Nothing has been recorded", out["why"])

    def test_a_failure_to_record_never_stops_the_work(self):
        """Applying an accommodation must not fail because a log file could
        not be opened. Losing the line is bad; not giving the student their
        time is worse."""
        with mock.patch.object(audit, "_record", side_effect=OSError("disk full")):
            self.assertEqual(audit.record(self.tmp, "a", "b", "c"), {})


class Reading(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audit.forget_actor()
        audit.set_actor_source(lambda: {}, {})
        audit.record(self.tmp, "accommodations", "applied", "Quiz one.",
                     students=[audit.person(900, "Dana Wu")])
        audit.record(self.tmp, "grade", "posted", "Wrote grades.",
                     students=[audit.person(901, "Jordan Alvarez")])
        audit.record(self.tmp, "a11y", "push", "Restyled 4 pages.")
        self.addCleanup(audit.forget_actor)

    def test_newest_first(self):
        self.assertEqual([r["seq"] for r in audit.read(self.tmp)], [3, 2, 1])

    def test_filtering_by_student_by_name_or_by_id(self):
        for needle in ("Dana Wu", "dana", "900"):
            rows = audit.read(self.tmp, student=needle)
            self.assertEqual([r["seq"] for r in rows], [1], needle)

    def test_filtering_by_part_of_the_studio(self):
        self.assertEqual([r["seq"] for r in audit.read(self.tmp, area="grade")], [2])

    def test_everyone_named_anywhere_is_offered_as_a_filter(self):
        self.assertEqual([p["name"] for p in audit.students_seen(self.tmp)],
                         ["Dana Wu", "Jordan Alvarez"])


class ToCanvas(unittest.TestCase):
    class Client:
        def __init__(self, fail=False):
            self.sent = []
            self.fail = fail

        def upload_user_file(self, name, payload, folder="", content_type=""):
            if self.fail:
                raise RuntimeError("Canvas said no")
            self.sent.append((name, folder, len(payload)))
            return {"id": len(self.sent), "url": "https://c/files/1?verifier=x"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audit.forget_actor()
        audit.set_actor_source(lambda: {}, {})
        audit.record(self.tmp, "a11y", "push", "Restyled 4 pages.")
        self.addCleanup(audit.forget_actor)

    def test_it_goes_to_your_own_user_files_with_a_readme(self):
        client = self.Client()
        out = audit.sync(client, self.tmp, "734975")
        names = [n for n, _f, _b in client.sent]
        self.assertIn("README.txt", names)
        self.assertTrue(any(n.startswith("course-734975-") and n.endswith(".jsonl")
                            for n in names), names)
        self.assertTrue(all(f == audit.FOLDER for _n, f, _b in client.sent))
        self.assertEqual(len(out["sent"]), 1)

    def test_an_unchanged_month_is_not_uploaded_twice(self):
        client = self.Client()
        audit.sync(client, self.tmp, "734975")
        before = len(client.sent)
        self.assertEqual(audit.sync(client, self.tmp, "734975")["sent"], [])
        self.assertEqual(len(client.sent), before, "it re-sent an unchanged file")

    def test_but_a_new_entry_is(self):
        client = self.Client()
        audit.sync(client, self.tmp, "734975")
        audit.record(self.tmp, "grade", "posted", "Wrote 3 grades.")
        self.assertEqual(len(audit.sync(client, self.tmp, "734975")["sent"]), 1)

    def test_canvas_refusing_is_reported_not_raised(self):
        out = audit.sync(self.Client(fail=True), self.tmp, "734975")
        self.assertEqual(out["sent"], [])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("Canvas said no", out["failed"][0]["error"])

    def test_the_readme_explains_what_the_files_are(self):
        self.assertIn("chain", audit.README)
        self.assertIn("--verify", audit.README)

    def test_the_syncer_does_nothing_when_it_is_turned_off(self):
        class App:
            cfg = type("C", (), {"data": "x", "audit_to_canvas": False})()
        out = audit.Syncer(App()).once()
        self.assertIn("skipped", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
