"""The two reports meant for somebody else, and the claims they may make.

Both are things an instructor shows a dean, which means a wrong number costs
more than a missing one. So what is asserted here is mostly restraint: the
score does not count what it has not looked at, and the policy check does not
claim compliance it cannot see.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import policies, score


class Scoring(unittest.TestCase):
    def test_a_scanned_pdf_scores_near_nothing(self):
        """No text layer at all is unusable, not merely awkward, and the number
        has to say so or the before/after gain is understated."""
        self.assertLessEqual(score.score_defects(["pdf_scanned"]), 20)

    def test_a_tagged_compliant_pdf_scores_full(self):
        self.assertEqual(score.score_defects([]), 100.0)

    def test_one_defect_twenty_times_is_not_twenty_defects(self):
        """One missing-alt problem repeated down a page is one thing to fix."""
        many = score.score_defects(["html_img_no_alt"] * 20)
        few = score.score_defects(["html_img_no_alt"] * score.REPEAT_CAP)
        self.assertEqual(many, few)
        self.assertGreater(many, 0)

    def test_it_never_goes_below_zero_or_above_a_hundred(self):
        self.assertEqual(score.score_defects(["pdf_scanned"] * 9), 0.0)
        self.assertEqual(score.score_defects([]), 100.0)

    def test_the_weights_are_published_with_the_number(self):
        """A dean can check arithmetic they can see. The report carries it."""
        self.assertIn("pdf_scanned", score.WEIGHTS)
        self.assertTrue(all(isinstance(v, int) for v in score.WEIGHTS.values()))


class WhatTheScoreRefusesToClaim(unittest.TestCase):
    class App:
        def __init__(self, root):
            self.root = Path(root)
            self.store = type("S", (), {"root": Path(root)})()
            self.cfg = type("C", (), {"data": str(root)})()

        def course_dir(self, cid):
            p = self.root / str(cid)
            p.mkdir(parents=True, exist_ok=True)
            return p

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.App(self.tmp)

    def test_a_course_nobody_scanned_gets_no_score_at_all(self):
        """Not a zero and not a hundred. Averaging over an assumption is how a
        report ends up saying something nobody checked."""
        out = score.forecast(self.app, 101)
        self.assertIsNone(out["before"])
        self.assertIsNone(out["after"])
        self.assertIn("Nothing in this course has been looked at", out["headline"])

    def test_it_names_what_it_did_not_count(self):
        out = score.forecast(self.app, 101)
        self.assertIn("PDFs", out["not_checked"])
        self.assertIn("Pages", out["not_checked"])
        self.assertIn("out of what was checked", out["caveat"])

    def test_it_says_plainly_that_it_is_not_ally(self):
        """The one claim that would get somebody caught out in front of a dean."""
        out = score.forecast(self.app, 101)
        self.assertIn("not Anthology's", out["method"])


class NotScannedIsThreeDifferentSentences(unittest.TestCase):
    """The bug this class exists for: a term's report showed "not scanned"
    against three courses and gave no way to tell whether that meant nobody had
    looked, the files were sitting there waiting, or the course had no files of
    that kind at all. Only the first two are work, and re-running the report --
    which reads this computer and never calls Canvas -- could never have
    changed any of them."""

    App = WhatTheScoreRefusesToClaim.App

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.App(self.tmp)

    def _listing(self, cid, rel, files):
        path = self.tmp / str(cid) / rel / "files.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": "now", "files": files, "folders": {}}),
                        encoding="utf-8")

    def test_nobody_asked_canvas_reads_as_never_looked_at(self):
        out = score.forecast(self.app, 101)
        pdf = next(p for p in out["parts"] if p["kind"] == "PDFs")
        self.assertEqual(pdf["state"], score.UNLISTED)
        self.assertIn("never looked at", pdf["note"])

    def test_listed_but_unopened_says_how_many_are_waiting(self):
        """The actionable case, and the one the old word hid: seventeen PDFs
        are on this machine's list and nobody has opened one of them."""
        self._listing(102, "pdf", [{"id": i} for i in range(17)])
        out = score.forecast(self.app, 102)
        pdf = next(p for p in out["parts"] if p["kind"] == "PDFs")
        self.assertEqual(pdf["state"], score.WAITING)
        self.assertIn("17 PDFs", pdf["note"])
        self.assertTrue(out["needs_scan"])
        self.assertIn("pdf", out["scan_kinds"])
        self.assertIn("17 PDFs", out["headline"])

    def test_a_course_with_no_powerpoints_is_not_a_gap(self):
        """Asked, and there are none. Printing that as "not scanned" sends
        somebody hunting for decks that do not exist."""
        self._listing(103, "docs/pptx", [])
        out = score.forecast(self.app, 103)
        pptx = next(p for p in out["parts"] if p["kind"] == "PowerPoint")
        self.assertEqual(pptx["state"], score.EMPTY)
        self.assertIn("PowerPoint", out["none_here"])
        self.assertNotIn("PowerPoint", out["not_checked"])

    def test_pages_are_never_offered_to_the_file_scan(self):
        """Pages come from the page pass, not the file scan, so offering to
        scan a course for them would be a button that changes nothing."""
        self._listing(104, "pdf", [{"id": 1}])
        out = score.forecast(self.app, 104)
        self.assertIn("Pages", out["not_checked"])
        self.assertNotIn("pages", out["scan_kinds"])

    def test_a_finished_course_is_not_offered_a_rescan(self):
        self._listing(105, "pdf", [])
        self._listing(105, "docs/pptx", [])
        self._listing(105, "docs/docx", [])
        out = score.forecast(self.app, 105)
        self.assertFalse(out["needs_scan"])
        self.assertEqual(out["scan_kinds"], [])


class PowerPointAndWordAreCountedToo(unittest.TestCase):
    """The bug this class exists for: a course of eleven tidy PDFs and
    twenty-five decks holding six hundred undescribed pictures scored 97.8,
    because only the PDFs were counted. The office weights had been written
    down and never wired to anything, and that number would have survived
    exactly as long as it took a dean to open one of the decks."""

    def test_the_office_weights_are_actually_used(self):
        self.assertLess(score.score_defects(["office_no_alt"] * 5),
                        score.score_defects([]))

    def test_a_deck_of_undescribed_pictures_does_not_score_full_marks(self):
        self.assertLess(score.score_defects(score._office_defects(26, 0, 0)), 80)

    def test_powerpoint_and_word_are_both_on_the_report(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        app = WhatTheScoreRefusesToClaim.App(tmp)
        kinds = [p["kind"] for p in score.forecast(app, 101)["parts"]]
        self.assertIn("PowerPoint", kinds)
        self.assertIn("Word", kinds)


class FixingSomethingNeverLowersItsScore(unittest.TestCase):
    """The bug this class exists for: a course of PDFs that arrived already
    tagged scored 97.8 before and 85.1 after, because veraPDF only ever runs on
    the fixed copy. The after score was charged for a failure the check
    discovered rather than caused, and "I ran the tool and the number went
    down" is the one result nobody can take to a dean."""

    class App:
        def __init__(self, root):
            self.root = Path(root)
            self.store = type("S", (), {"root": Path(root)})()
            self.cfg = type("C", (), {"data": str(root)})()

        def course_dir(self, cid):
            p = self.root / str(cid)
            p.mkdir(parents=True, exist_ok=True)
            return p

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.App(self.tmp)

    def _course(self, class_before, compliant):
        wd = self.tmp / "77" / "pdf"
        sub = wd / "900"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "fixed.pdf").write_bytes(b"%PDF-1.4 fixed")
        (sub / "result.json").write_text(json.dumps(
            {"class_before": class_before, "status": "ok"}), encoding="utf-8")
        (wd / "files.json").write_text(json.dumps(
            {"files": [{"id": "900", "display_name": "handout.pdf"}]}), encoding="utf-8")
        (wd / "validation.json").write_text(json.dumps(
            {"per_file": [{"dir": "900", "compliant": compliant}]}), encoding="utf-8")
        (wd / "alt-todo.json").write_text("{}", encoding="utf-8")
        return score.pdfs(self.app, 77)["rows"][0]

    def test_an_already_tagged_pdf_that_fails_ua_does_not_lose_points(self):
        row = self._course("tagged", False)
        self.assertGreaterEqual(row["after"], row["before"])
        self.assertFalse(row["ua_counted"], "it counted a verdict it cannot compare")

    def test_an_untagged_pdf_that_now_passes_gains_a_lot(self):
        """PDF/UA-1 needs a tag tree, so an untagged original certainly failed.
        That is a fact rather than an assumption, and both sides carry it."""
        row = self._course("text-untagged", True)
        self.assertTrue(row["ua_counted"])
        self.assertEqual(row["after"], 100.0)
        self.assertLess(row["before"], 40)

    def test_an_untagged_pdf_that_still_fails_gains_less(self):
        both = self._course("text-untagged", False)
        self.assertGreater(both["after"], both["before"])
        self.assertLess(both["after"], 100.0)


class SyllabusPolicies(unittest.TestCase):
    def setUp(self):
        self.policies = policies.load(None)

    def test_the_list_is_data_not_code(self):
        self.assertTrue(policies.DEFAULTS.is_file())
        raw = json.loads(policies.DEFAULTS.read_text(encoding="utf-8"))
        self.assertIn("policies", raw)
        self.assertIn("_note", raw)

    def test_markup_does_not_hide_a_statement(self):
        body = "<p><strong>Disability Services</strong>: contact the office.</p>"
        out = policies.check_body(body, self.policies)
        found = {r["id"]: r["present"] for r in out["rows"]}
        self.assertTrue(found["ada"])

    def test_the_real_wording_that_slipped_through_once(self):
        """"Grading uses a 10-point scale" is a grading scale statement. The
        first pattern set wanted the literal words "grading scale" and reported
        five clean syllabi as five failures, which is the kind of false alarm
        that makes somebody stop reading the report."""
        out = policies.check_body(
            "Course Evaluation and Grading. Grading uses a 10-point scale. "
            "Final grades are rounded to the nearest 1%.", self.policies)
        found = {r["id"]: r["present"] for r in out["rows"]}
        self.assertTrue(found["grading"])

    def test_but_merely_saying_the_word_grade_is_not_a_scale(self):
        """The opposite failure: a check that always passes finds nothing."""
        out = policies.check_body(
            "Welcome. Bring a laptop. I grade fairly and return work quickly.",
            self.policies)
        found = {r["id"]: r["present"] for r in out["rows"]}
        self.assertFalse(found["grading"])

    def test_an_empty_syllabus_is_reported_as_empty(self):
        out = policies.check_body("", self.policies)
        self.assertTrue(out["empty"])
        self.assertEqual(out["present"], 0)

    def test_a_match_carries_the_words_it_matched_on(self):
        """So somebody can judge whether the statement is the right one, which
        this tool cannot do for them."""
        out = policies.check_body("Title IX coordinator is in Room 4.", self.policies)
        row = next(r for r in out["rows"] if r["id"] == "titleix")
        self.assertTrue(row["present"])
        self.assertIn("Title IX", row["excerpt"])

    def test_a_broken_pattern_does_not_take_the_report_down(self):
        out = policies.check_body("anything", [{"id": "x", "label": "X", "why": "",
                                                "any": ["(unclosed"]}])
        self.assertEqual(out["rows"][0]["present"], False)

    def test_nothing_here_claims_compliance(self):
        """A tick means the subject is mentioned. Only somebody holding the
        catalogue can say whether the wording is the approved one, and the
        report has to say that where the report is read."""
        class App:
            cfg = type("C", (), {"base_url": "https://x.edu"})()
            content = type("C", (), {"course_detail": staticmethod(
                lambda cid, include=None: {"name": "A course",
                                           "syllabus_body": "Office hours: Tuesday."})})()

        out = policies.check_many(App(), ["1"])
        self.assertIn("does not mean the wording", out["note"])
        self.assertIn("Nothing here changes a syllabus", out["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
