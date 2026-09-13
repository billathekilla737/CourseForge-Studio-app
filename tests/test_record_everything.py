"""What the record has to contain, and when it reaches Canvas.

The account exists to answer a challenge a year later. Until now it held the
moment a number reached Canvas and nothing about how the number was arrived
at: a model read the work, a curve moved every total, a hand overruled what
was proposed, a spreadsheet carried the lot off the machine, and none of it
left a trace. A push entry cannot answer "who decided this".
"""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from courseforge import audit


class Client:
    """Counts uploads and remembers the names, nothing more."""

    def __init__(self):
        self.uploaded = []

    def upload_user_file(self, name, payload, folder=None, content_type=None):
        self.uploaded.append(name)
        return {"id": len(self.uploaded), "url": "https://x/f/%d?v=1" % len(self.uploaded)}


class App:
    def __init__(self, root):
        self.cfg = type("C", (), {"data": str(root), "audit_to_canvas": True})()
        self.client = Client()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audit.CHANGED.clear()
        self.addCleanup(audit.CHANGED.clear)


class EveryCourseReachesCanvasNotJustTheAccount(Base):
    def test_a_course_record_is_uploaded_under_its_own_name(self):
        audit.record(self.tmp / "734975", "grade", "drafted", "Claude proposed 3 grades.")
        audit.record(self.tmp, "accommodations", "roster", "Changed the list.")
        app = App(self.tmp)
        out = audit.Syncer(app).once()["courses"]
        self.assertIn("734975", out)
        self.assertIn("account", out)
        names = [n for n in app.client.uploaded if n.endswith(".jsonl")]
        self.assertTrue(any(n.startswith("course-734975-") for n in names), names)
        self.assertTrue(any(n.startswith("account-") for n in names), names)

    def test_a_course_with_no_record_is_not_uploaded(self):
        audit.record(self.tmp / "734975", "grade", "drafted", "Claude proposed 3 grades.")
        (self.tmp / "999").mkdir(parents=True, exist_ok=True)
        app = App(self.tmp)
        out = audit.Syncer(app).once()["courses"]
        self.assertNotIn("999", out)

    def test_an_unchanged_month_is_not_sent_twice(self):
        audit.record(self.tmp / "734975", "grade", "drafted", "One.")
        app = App(self.tmp)
        syncer = audit.Syncer(app)
        syncer.once()
        first = len(app.client.uploaded)
        syncer.once()
        self.assertEqual(len(app.client.uploaded), first, "re-sent a file nothing changed")

    def test_a_new_entry_is_sent_on_the_next_pass(self):
        audit.record(self.tmp / "734975", "grade", "drafted", "One.")
        app = App(self.tmp)
        syncer = audit.Syncer(app)
        syncer.once()
        before = len(app.client.uploaded)
        audit.record(self.tmp / "734975", "grade", "edited", "Two.")
        syncer.once()
        self.assertGreater(len(app.client.uploaded), before)

    def test_turning_it_off_in_config_stops_the_upload(self):
        audit.record(self.tmp / "734975", "grade", "drafted", "One.")
        app = App(self.tmp)
        app.cfg.audit_to_canvas = False
        out = audit.Syncer(app).once()
        self.assertIn("skipped", out)
        self.assertEqual(app.client.uploaded, [])


class WritingAnEntryWakesTheSyncer(Base):
    """Three minutes is long enough to close a laptop in."""

    def test_recording_sets_the_flag(self):
        self.assertFalse(audit.CHANGED.is_set())
        audit.record(self.tmp / "734975", "grade", "drafted", "One.")
        self.assertTrue(audit.CHANGED.is_set())

    def test_the_flag_is_an_event_the_syncer_can_wait_on(self):
        self.assertIsInstance(audit.CHANGED, threading.Event)

    def test_the_syncer_settles_before_uploading_a_burst(self):
        s = audit.Syncer(App(self.tmp), every_s=180)
        self.assertGreaterEqual(s.settle_s, 2)
        self.assertLess(s.settle_s, s.every_s)


class TheChainSurvivesTheNewEntries(Base):
    def test_many_entries_in_one_course_verify(self):
        root = self.tmp / "734975"
        for i in range(12):
            audit.record(root, "grade", "edited", "Changed the score, edit %d." % i)
        out = audit.verify(root)
        self.assertTrue(out["ok"], out.get("why"))
        self.assertEqual(out["entries"], 12)

    def test_a_changed_entry_still_breaks_it(self):
        root = self.tmp / "734975"
        for i in range(4):
            audit.record(root, "grade", "edited", "Edit %d." % i)
        path = audit.months(root)[0]
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["sentence"] = "Edit 1, but different."
        lines[1] = json.dumps(row, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(audit.verify(root)["ok"])


class TheGradingDecisionsAreRecorded(unittest.TestCase):
    """Each of these was invisible before: the record began at the push."""

    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent / "courseforge")

    def test_a_grading_run_names_the_model_and_the_rubric(self):
        s = (self.src / "grader.py").read_text(encoding="utf-8")
        self.assertIn('"grade", "drafted"', s)
        self.assertIn('"models": models', s)
        self.assertIn('"rubric": [c.get("label") for c in rubric]', s)
        self.assertIn("Nothing was sent to Canvas", s)

    def test_a_curve_is_recorded(self):
        s = (self.src / "server.py").read_text(encoding="utf-8")
        self.assertIn('"grade", "curved"', s)

    def test_a_hand_edit_over_the_model_is_recorded(self):
        s = (self.src / "server.py").read_text(encoding="utf-8")
        self.assertIn('"grade", "edited"', s)
        self.assertIn("over what Claude proposed", s)

    def test_an_export_is_recorded(self):
        s = (self.src / "server.py").read_text(encoding="utf-8")
        self.assertIn('"grade", "exported"', s)

    def test_an_edit_that_changed_nothing_writes_no_entry(self):
        """Sliders fire on every drag. Only a real change is worth a line."""
        s = (self.src / "server.py").read_text(encoding="utf-8")
        self.assertIn('if was_total != entry.get("total") or was_comment', s)


if __name__ == "__main__":
    unittest.main()
