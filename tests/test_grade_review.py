"""Regressions from the Grade area review. No network: Canvas is a fake.

Each class pins one thing that was wrong:

  * a slider moved under a curve left the curved total where it was, so the
    roster and a push disagreed about the same student;
  * removing a handoff deleted files from Canvas with no second click;
  * the handoff bundle's folder check was a string prefix, not a path test;
  * grade.js called the browser's confirm(), and declared one function twice
    so the schedule footer read "2954 weeks ago".
"""
import io
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from courseforge import confirm, handoff
from courseforge.server import App
from courseforge.store import Store

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web"


def bare_app(root: Path, client=None, writes: bool = True) -> App:
    """An App with only the parts these methods touch. The real constructor
    starts an audit thread and installs every area, none of which is wanted
    in a unit test."""
    app = App.__new__(App)
    app.store = Store(root)
    app.cfg = SimpleNamespace(allow_canvas_writes=writes, handoff_folder="canvas-grader")
    app.confirm = confirm.ConfirmGate()
    app._client = client
    return app


RUBRIC = [{"id": "c1", "label": "Form", "points": 10.0, "ratings": []},
          {"id": "c2", "label": "Finish", "points": 10.0, "ratings": []}]


class EditStudentKeepsTheCurvedTotalInStep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        self.app.store.save_draft("1", "2", {
            "rubric": RUBRIC, "points_possible": 20,
            "students": {
                "7": {"user_id": "7", "source": "claude", "scores": {"c1": 5, "c2": 5},
                      "total": 10, "final_total": 12,
                      "curve": {"flat": 2, "by_criterion": {}, "steps": [{"kind": "flat"}]}},
                "8": {"user_id": "8", "source": "claude", "scores": {"c1": 5, "c2": 5},
                      "total": 10},
            }})

    def test_a_slider_moves_the_curved_total_too(self):
        entry = self.app.edit_student("1", "2", "7", {"scores": {"c1": 8, "c2": 5},
                                                     "comment": "closer"})
        self.assertEqual(entry["total"], 13)
        self.assertEqual(entry["final_total"], 15, "earned 13 plus the flat +2")
        self.assertEqual(entry["source"], "human")
        self.assertFalse(entry["total_only"])
        saved = self.app.store.draft("1", "2")["students"]["7"]
        self.assertEqual(saved["final_total"], 15)

    def test_a_curved_total_is_capped_at_points_possible(self):
        entry = self.app.edit_student("1", "2", "7", {"scores": {"c1": 10, "c2": 10}})
        self.assertEqual(entry["total"], 20)
        self.assertEqual(entry["final_total"], 20, "a curve cannot invent 22 of 20")

    def test_no_curve_means_no_final_total_is_invented(self):
        entry = self.app.edit_student("1", "2", "8", {"scores": {"c1": 9, "c2": 5}})
        self.assertEqual(entry["total"], 14)
        self.assertNotIn("final_total", entry)

    def test_a_comment_alone_leaves_the_scores_alone(self):
        entry = self.app.edit_student("1", "2", "7", {"comment": "see me"})
        self.assertEqual(entry["scores"], {"c1": 5, "c2": 5})
        self.assertEqual(entry["total"], 10)
        self.assertEqual(entry["final_total"], 12)
        self.assertEqual(entry["comment"], "see me")


class FakeFiles:
    """The two Canvas calls handoff_disable makes, and a record of the deletes."""

    def __init__(self, files):
        self.files = files
        self.deleted = []

    def user_folder_files(self, folder):
        return list(self.files)

    def delete_file(self, file_id):
        self.deleted.append(str(file_id))
        return {}


class RemovingAHandoffAsksFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = FakeFiles([
            {"id": 11, "display_name": handoff.draft_name("1", "2")},
            {"id": 12, "display_name": handoff.blend_name("1", "2")},
            {"id": 13, "display_name": handoff.draft_name("1", "3")},   # another assignment
        ])
        self.app = bare_app(self.tmp, self.client)
        self.adir = self.app.store.assignment_dir("1", "2")
        handoff.save_state(self.adir, rev=3)

    def test_the_first_call_is_refused_and_deletes_nothing(self):
        with self.assertRaises(confirm.ConfirmRequired) as caught:
            self.app.handoff_disable("1", "2", remove=True)
        self.assertEqual(self.client.deleted, [])
        self.assertIn("2 handoff file(s)", caught.exception.summary)
        self.assertTrue(handoff.enabled(self.adir), "still carried until it goes through")

    def test_the_second_call_with_the_token_deletes_only_this_assignment(self):
        try:
            self.app.handoff_disable("1", "2", remove=True)
        except confirm.ConfirmRequired as asked:
            token = asked.token
        out = self.app.handoff_disable("1", "2", remove=True, confirm_token=token)
        self.assertEqual(out, {"ok": True, "removed": 2})
        self.assertEqual(sorted(self.client.deleted), ["11", "12"])
        self.assertFalse(handoff.enabled(self.adir))

    def test_stopping_without_removing_never_touches_canvas(self):
        out = self.app.handoff_disable("1", "2", remove=False)
        self.assertEqual(out, {"ok": True, "removed": 0})
        self.assertEqual(self.client.deleted, [])
        self.assertFalse(handoff.enabled(self.adir))

    def test_nothing_in_canvas_means_nothing_to_confirm(self):
        self.client.files = []
        out = self.app.handoff_disable("1", "2", remove=True)
        self.assertEqual(out["removed"], 0)

    def test_the_hard_lock_wins_over_the_confirmation(self):
        app = bare_app(self.tmp, self.client, writes=False)
        with self.assertRaises(PermissionError):
            app.handoff_disable("1", "2", remove=True)
        self.assertEqual(self.client.deleted, [])


class TheBlendBundleStaysInsideItsFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.adir = self.tmp / "data" / "1" / "2"
        self.adir.mkdir(parents=True)

    def bundle(self, names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in names:
                zf.writestr(name, "{}")
        return buf.getvalue()

    def test_a_sibling_folder_sharing_the_prefix_is_refused(self):
        # "data/1/2" is a string prefix of "data/1/23", which is what the old
        # startswith() check let through.
        raw = self.bundle(["blend/9/model/stats.json",
                           "../23/blend/9/model/stats.json",
                           "../../../elsewhere/blend/9/model/contact.png"])
        written = handoff.apply_blend_zip(self.adir, raw)
        self.assertEqual(written, 1)
        self.assertTrue((self.adir / "blend" / "9" / "model" / "stats.json").is_file())
        self.assertFalse((self.tmp / "data" / "1" / "23").exists())
        self.assertFalse((self.tmp / "elsewhere").exists())

    def test_only_the_named_parts_are_unpacked(self):
        raw = self.bundle(["blend/9/model/stats.json", "blend/9/model/evil.py"])
        self.assertEqual(handoff.apply_blend_zip(self.adir, raw), 1)
        self.assertFalse((self.adir / "blend" / "9" / "model" / "evil.py").exists())


class GradeJsKeepsToTheContract(unittest.TestCase):
    """There is no JavaScript runner here; these read the source."""

    @classmethod
    def setUpClass(cls):
        cls.src = (WEB / "js" / "grade.js").read_text(encoding="utf-8")

    def test_no_browser_confirm_or_alert(self):
        """The front-end contract: askConfirm and banners, never confirm()."""
        calls = re.findall(r"(?<![\w.$])(confirm|alert)\(", self.src)
        self.assertEqual(calls, [])

    def test_no_location_reload(self):
        self.assertNotIn("location.reload(", self.src)

    def test_each_top_level_function_is_declared_once(self):
        """Two declarations of one name in a plain script: the later one wins
        everywhere, and the first caller gets the wrong function without any
        error. That is how ago(seconds) came to be handed to ago(iso)."""
        names = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                           self.src, flags=re.MULTILINE)
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [])

    def test_the_schedule_footer_is_fed_seconds_by_name(self):
        self.assertIn("agoSeconds(sc.age_s)", self.src)

    def test_the_announcement_dialog_tolerates_no_schedule(self):
        body = self.src[self.src.index("function openAnnounce("):]
        body = body[:body.index("\n}\n")]
        unguarded = re.findall(r"(?<!&& )S\.sched\.items", body)
        self.assertEqual(unguarded, [])
        self.assertIn("(S.sched && S.sched.items)", body)


if __name__ == "__main__":
    unittest.main()
