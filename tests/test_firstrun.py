"""The first-run offer: whether it appears, once, and what it refuses to run.

No installer is ever started here, and the real per-user folder is never
touched: the marker path is redirected at a temporary directory.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from courseforge import tools
from courseforge.config import Config

REPO = Path(__file__).resolve().parent.parent


class FirstRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = Config.load(REPO / "config.example.json")
        self.patch = mock.patch.object(tools, "_setup_path", lambda: self.tmp / "tools-setup.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_offers_when_something_is_missing_and_only_once(self):
        with mock.patch.object(tools, "install_plan", lambda c=None: {
                "winget": True, "rows": [{"tool": "tesseract", "label": "Tesseract OCR",
                                          "how": "winget", "package": "X", "command": "c"}],
                "found": {}}):
            self.assertIsNotNone(tools.should_offer_setup(self.cfg), "it did not offer")
            tools.remember_setup(asked=True)
            self.assertIsNone(tools.should_offer_setup(self.cfg), "it asked twice")

    def test_never_ask_again_sticks(self):
        tools.remember_setup(asked=False, never=True)
        with mock.patch.object(tools, "install_plan", lambda c=None: {
                "winget": True, "rows": [{"tool": "java", "label": "J", "how": "winget",
                                          "package": "X", "command": "c"}], "found": {}}):
            self.assertIsNone(tools.should_offer_setup(self.cfg))

    def test_nothing_missing_is_recorded_without_asking(self):
        with mock.patch.object(tools, "install_plan", lambda c=None: {"winget": True, "rows": [], "found": {}}):
            self.assertIsNone(tools.should_offer_setup(self.cfg))
        self.assertTrue(tools.setup_state().get("asked"), "it will ask again next launch")

    def test_a_broken_marker_file_does_not_crash_the_launch(self):
        (self.tmp / "tools-setup.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(tools.setup_state(), {})

    def test_install_stream_reports_a_failure_instead_of_raising(self):
        lines = []
        plan = {"winget": True, "found": {},
                "rows": [{"tool": "tesseract", "label": "Tesseract OCR", "how": "winget",
                          "package": "X", "command": "c"}]}
        class Proc:
            stdout = iter(["downloading\n", "\n"])
            def wait(self): return 1
        with mock.patch.object(tools, "install_plan", lambda c=None: plan), \
             mock.patch.object(tools.shutil, "which", lambda n: "winget.exe"), \
             mock.patch.object(tools.subprocess, "Popen", lambda *a, **k: Proc()):
            out = tools.install_stream(self.cfg, on_line=lines.append)
        self.assertEqual(out["failed"], ["tesseract"])
        self.assertEqual(out["installed"], [])
        self.assertTrue(any("did not install" in l for l in lines), lines)

    def test_manual_only_tool_is_never_run(self):
        ran = []
        plan = {"winget": True, "found": {},
                "rows": [{"tool": "verapdf", "label": "veraPDF", "how": "manual",
                          "package": "", "command": "", "steps": tools.VERAPDF_STEPS}]}
        with mock.patch.object(tools, "install_plan", lambda c=None: plan), \
             mock.patch.object(tools.subprocess, "Popen", lambda *a, **k: ran.append(a)):
            out = tools.install_stream(self.cfg, on_line=lambda t: None)
        self.assertEqual(ran, [], "it tried to run a manual step")
        self.assertEqual(out["manual"], ["verapdf"])
        self.assertIn("verapdf.org", out["steps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
