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



class SetupWindowLayout(unittest.TestCase):
    """The window must always keep an answer in it.

    It was built heading-first, which meant a longer list of tools pushed the
    three buttons off the bottom edge and left no way to say no. Pack order is
    priority when there is not enough room, so the footer is claimed first.
    Skipped where there is no display to draw on.
    """

    def setUp(self):
        self.tk = __import__("tkinter")
        try:
            probe = self.tk.Tk()
            probe.destroy()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"no display: {exc}")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = Config.load(REPO / "config.example.json")
        self.patch = mock.patch.object(tools, "_setup_path",
                                       lambda: self.tmp / "tools-setup.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    @staticmethod
    def _plan(n):
        return {"winget": True, "found": {}, "rows": [
            {"tool": f"tool{i}", "label": f"A tool with a fairly long name {i}",
             "how": "winget", "package": "X", "command": "c"} for i in range(n)]}

    def _buttons_inside(self, rows, height):
        from courseforge import setup_window
        win = setup_window.SetupWindow(self.cfg, self._plan(rows))
        win.root.geometry(f"520x{height}")
        # update(), not update_idletasks(): the window has to be mapped before
        # winfo_rooty means anything, and without a mainloop it is not.
        win.root.update()
        wh = win.root.winfo_height()
        out = []
        for name, b in (("Not now", win.skip_btn), ("Never ask again", win.never_btn),
                        ("Install now", win.go_btn)):
            bottom = b.winfo_rooty() - win.root.winfo_rooty() + b.winfo_height()
            out.append((name, b.winfo_ismapped() and 0 < bottom <= wh, bottom, wh))
        win.root.destroy()
        return out

    def test_the_buttons_stay_in_the_window(self):
        for rows in (1, 3, 6):
            for height in (460, 380):
                for name, ok, bottom, wh in self._buttons_inside(rows, height):
                    self.assertTrue(ok, f"{name} fell out of a {height}px window "
                                        f"listing {rows} tools (bottom {bottom} of {wh})")

    def test_the_heading_counts_what_it_lists(self):
        from courseforge import setup_window
        for rows, expected in ((1, "One optional tool is missing"),
                               (4, "4 optional tools are missing")):
            win = setup_window.SetupWindow(self.cfg, self._plan(rows))
            win.root.update()
            texts = [w.cget("text") for w in win.root.winfo_children()
                     if isinstance(w, self.tk.Label)]
            win.root.destroy()
            self.assertIn(expected, texts)

if __name__ == "__main__":
    unittest.main(verbosity=2)
