"""The first-run offer: whether it appears, once, and what it refuses to run.

No installer is ever started here, and the real per-user folder is never
touched: the marker path is redirected at a temporary directory.
"""
import os
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
                "rows": [{"tool": "somebody-elses", "label": "Something", "how": "manual",
                          "package": "", "command": "", "steps": tools.VERAPDF_STEPS}]}
        with mock.patch.object(tools, "install_plan", lambda c=None: plan), \
             mock.patch.object(tools.subprocess, "Popen", lambda *a, **k: ran.append(a)):
            out = tools.install_stream(self.cfg, on_line=lambda t: None)
        self.assertEqual(ran, [], "it tried to run a manual step")
        self.assertEqual(out["manual"], ["somebody-elses"])
        self.assertIn("verapdf.org", out["steps"])

    def test_verapdf_goes_through_its_own_installer_not_a_package_manager(self):
        """The one tool no package manager carries. It must not reach winget,
        and it must not be silently skipped as a manual step either -- that is
        what it was, and what made clicking Install leave it uninstalled."""
        from courseforge import verapdf_setup
        ran, called = [], []
        plan = {"winget": True, "found": {},
                "rows": [{"tool": "verapdf", "label": "veraPDF", "how": "download",
                          "package": verapdf_setup.RELEASE, "command": "",
                          "steps": tools.VERAPDF_STEPS, "into": "somewhere"}]}
        with mock.patch.object(tools, "install_plan", lambda c=None: plan), \
             mock.patch.object(tools.subprocess, "Popen", lambda *a, **k: ran.append(a)), \
             mock.patch.object(verapdf_setup, "install",
                               lambda cfg=None, on_line=None, dest=None:
                                   (called.append(True),
                                    {"ok": True, "path": "v", "dir": "d", "detail": ""})[1]):
            out = tools.install_stream(self.cfg, on_line=lambda t: None)
        self.assertEqual(ran, [], "veraPDF was handed to a package manager")
        self.assertEqual(called, [True], "the veraPDF installer was never called")
        self.assertEqual(out["installed"], ["verapdf"])
        self.assertEqual(out["manual"], [])



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

class AlreadyInstalled(unittest.TestCase):
    """The window must not appear on a machine that already has the tools.

    This is a live check of what is on disk, not a remembered flag, so it also
    has to survive the ordinary case of a tool being installed but not on PATH.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = Config.load(REPO / "config.example.json")
        self.patch = mock.patch.object(tools, "_setup_path",
                                       lambda: self.tmp / "tools-setup.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_nothing_is_offered_when_everything_is_present(self):
        present = {k: dict(v, ok=True) for k, v in tools.detect(self.cfg).items()}
        with mock.patch.object(tools, "detect", lambda c=None: present):
            self.assertEqual(tools.install_plan(self.cfg)["rows"], [])
            self.assertIsNone(tools.should_offer_setup(self.cfg),
                              "it offered to install tools that are already here")

    def test_a_tool_off_the_path_still_counts_as_installed(self):
        # Temurin's installer makes "add to PATH" a choice, and a managed
        # install often declines it. The tool is there; do not ask for it again.
        root = self.tmp / "Eclipse Adoptium"
        exe = root / "jre-21.0.12.7-hotspot" / "bin" / "java.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        env = {k: v for k, v in os.environ.items() if k != "JAVACMD"}
        with mock.patch.object(tools.shutil, "which", lambda n: None),              mock.patch.dict(os.environ, env, clear=True),              mock.patch.object(tools, "java_dirs", lambda: [(root, "*/bin/java.exe")]):
            self.assertTrue(tools.detect(self.cfg)["java"]["ok"], "an installed Java was missed")
            offered = [r["tool"] for r in tools.install_plan(self.cfg)["rows"]]
        self.assertNotIn("java", offered, "it offered a Java that is already installed")

    def test_a_missing_tool_is_still_reported_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "JAVACMD"}
        with mock.patch.object(tools.shutil, "which", lambda n: None),              mock.patch.dict(os.environ, env, clear=True),              mock.patch.object(tools, "java_dirs", lambda: [(self.tmp / "empty", "*/java.exe")]):
            self.assertFalse(tools.detect(self.cfg)["java"]["ok"])


class ClosingIsNotAnswering(unittest.TestCase):
    """Shutting the window must not count as declining the offer.

    The titlebar X used to run the same handler as Not now, so closing the
    window wrote down a permanent answer. When a layout bug put the buttons out
    of reach, the X was the only way out -- and taking it silently turned the
    offer off for good on a machine that had none of the tools.
    """

    def setUp(self):
        try:
            probe = __import__("tkinter").Tk()
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
        self.plan = {"winget": True, "found": {}, "rows": [
            {"tool": "tesseract", "label": "Tesseract OCR", "how": "winget",
             "package": "X", "command": "c"}]}

    def _window(self):
        from courseforge import setup_window
        win = setup_window.SetupWindow(self.cfg, self.plan)
        win.root.update()
        return win

    def test_the_close_box_writes_nothing_down(self):
        win = self._window()
        # exactly what the window manager calls when the X is clicked
        win.root.protocol("WM_DELETE_WINDOW")
        win._dismiss()
        self.assertEqual(tools.setup_state(), {}, "closing the window recorded an answer")
        self.assertEqual(win.answer, "dismissed")

    def test_so_the_offer_comes_back_next_launch(self):
        self._window()._dismiss()
        with mock.patch.object(tools, "install_plan", lambda c=None: self.plan):
            self.assertIsNotNone(tools.should_offer_setup(self.cfg),
                                 "the offer vanished after the window was merely closed")

    def test_but_not_now_is_an_answer_and_sticks(self):
        self._window()._skip()
        self.assertTrue(tools.setup_state().get("asked"))
        with mock.patch.object(tools, "install_plan", lambda c=None: self.plan):
            self.assertIsNone(tools.should_offer_setup(self.cfg),
                              "Not now did not stop it asking again")

    def test_and_the_answer_can_be_thrown_away(self):
        self._window()._skip()
        tools.forget_setup()
        with mock.patch.object(tools, "install_plan", lambda c=None: self.plan):
            self.assertIsNotNone(tools.should_offer_setup(self.cfg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
