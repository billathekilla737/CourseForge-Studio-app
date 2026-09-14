"""The in-app updater: what it refuses, and what it leaves alone.

An updater is the one feature that can destroy the install it runs in, and it
runs on machines whose owner cannot recover from that. So most of this is about
the refusals: a folder somebody is working in, an archive that is not this app,
an archive that tries to write outside the folder, and the user's own config
and data, which must survive an update that replaces everything around them.
"""
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from courseforge import update


class Cfg:
    update_repo = "someone/CourseForge-Studio"
    update_branch = "main"
    update_token = ""
    check_updates = True


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()
        return False


def archive(top="repo-abc123", files=None, extra=None) -> bytes:
    """A zip shaped the way GitHub's zipball is: everything under one folder."""
    files = files if files is not None else {
        "courseforge/__init__.py": '__version__ = "9.9.9"\n',
        "courseforge/server.py": "# new server\n",
        "courseforge/launcher.py": "# new launcher\n",
        "README.md": "# new readme\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/", "")
        for name, body in files.items():
            zf.writestr(f"{top}/{name}", body)
        for name, body in (extra or {}).items():
            zf.writestr(name, body)          # deliberately outside `top`
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = Cfg()
        # An install that is not a git checkout: the colleague's case.
        (self.tmp / "courseforge").mkdir()
        (self.tmp / "courseforge" / "__init__.py").write_text(
            '__version__ = "1.0.0"\n', encoding="utf-8")
        (self.tmp / "courseforge" / "server.py").write_text("# old\n", encoding="utf-8")
        (self.tmp / "courseforge" / "launcher.py").write_text("# old\n", encoding="utf-8")

    def serve(self, payload: bytes):
        """Point the updater's one network seam at bytes we control."""
        def fake(url, token="", accept=""):
            return FakeResponse(payload)
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake

    def ready(self) -> update.Status:
        return update.Status(checked=True, available=True, current="aaaaaaa",
                             latest="bbbbbbb")


class WhatItRefusesToOverwrite(Base):
    def test_an_archive_that_is_not_this_app(self):
        self.serve(archive(files={"README.md": "# something else\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("not CourseForge Studio", out["message"])
        self.assertEqual((self.tmp / "courseforge" / "server.py").read_text(), "# old\n")

    def test_an_archive_that_writes_outside_the_folder(self):
        """A zip entry with .. in it, the oldest trick there is.

        Written INSIDE the top-level folder, so the one-top-level check
        passes and the traversal guard is the thing actually under test.
        """
        self.serve(archive(extra={"repo-abc123/../evil.py": "pwned\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("outside", out["message"])
        self.assertFalse((self.tmp.parent / "evil.py").exists())

    def test_an_absolute_entry_is_refused(self):
        self.serve(archive(extra={"/etc/passwd": "no\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])

    def test_the_traversal_check_itself(self):
        self.assertTrue(update._unsafe("top/../evil.py", "top"))
        self.assertTrue(update._unsafe("/abs.py", "top"))
        self.assertTrue(update._unsafe("elsewhere/x.py", "top"))
        self.assertFalse(update._unsafe("top/courseforge/server.py", "top"))

    def test_an_archive_with_several_top_level_folders(self):
        self.serve(archive(extra={"other/thing.py": "x\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertFalse(out["restart"])

    def test_something_that_is_not_a_zip_at_all(self):
        self.serve(b"<html>404</html>")
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("not readable", out["message"])

    def test_a_status_that_says_do_not(self):
        self.serve(archive())
        blocked = update.Status(can_apply=False, why_not="because I said so")
        out = update.apply(self.cfg, blocked, root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "because I said so")


class TheGuardOnALocalCheckout(Base):
    def test_a_clean_folder_is_safe(self):
        status = update.Status()
        self.assertTrue(update._guard_local_edits(status, self.tmp))
        self.assertTrue(status.can_apply)

    def test_the_guard_runs_before_the_network(self):
        """A check that cannot reach GitHub must still report the refusal.

        Asked the other way round, an offline machine gets a status saying it
        is safe to overwrite a folder full of uncommitted work.
        """
        src = (Path(update.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def check("):src.index("def _guard_local_edits(")]
        self.assertLess(body.index("_guard_local_edits(status, root)"),
                        body.index("_get("),
                        "the guard moved after the network call")

    def test_apply_checks_the_disk_itself(self):
        """It must not trust a can_apply flag handed in by a caller."""
        src = (Path(update.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def apply("):src.index("def _unsafe(")]
        self.assertIn("_guard_local_edits(fresh, root)", body)


class WhatSurvivesAnUpdate(Base):
    def test_the_config_is_not_replaced(self):
        (self.tmp / "config.json").write_text('{"port": 8900}', encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", "config.json": '{"port": 1}'}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual(json.loads((self.tmp / "config.json").read_text())["port"], 8900)

    def test_the_data_directory_is_not_touched(self):
        (self.tmp / "data" / "734975").mkdir(parents=True)
        (self.tmp / "data" / "734975" / "draft.json").write_text("mine", encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", "data/734975/draft.json": "theirs"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual((self.tmp / "data" / "734975" / "draft.json").read_text(), "mine")

    def test_the_git_folder_is_not_touched(self):
        (self.tmp / ".git").mkdir()
        (self.tmp / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", ".git/HEAD": "clobbered"}))
        update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertEqual((self.tmp / ".git" / "HEAD").read_text(), "ref: refs/heads/main")


class AGoodUpdate(Base):
    def test_the_files_are_replaced(self):
        self.serve(archive())
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertTrue(out["restart"])
        self.assertEqual((self.tmp / "courseforge" / "server.py").read_text(),
                         "# new server\n")
        self.assertEqual((self.tmp / "README.md").read_text(), "# new readme\n")

    def test_no_half_written_files_are_left_behind(self):
        self.serve(archive())
        update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertEqual(list(self.tmp.rglob("*.new")), [])

    def test_the_folder_learns_which_version_it_is(self):
        """Without git there is nothing else to compare against next time."""
        self.serve(archive())
        update.apply(self.cfg, self.ready(), root=self.tmp)
        stamp = update.read_stamp(self.tmp)
        self.assertEqual(stamp["revision"], "bbbbbbb")
        self.assertEqual(update.current_revision(self.tmp), "bbbbbbb")

    def test_a_folder_with_no_stamp_and_no_git_knows_nothing(self):
        self.assertEqual(update.current_revision(self.tmp), "")


class WhatTheWindowIsToldWhenItGoesWrong(Base):
    def test_a_private_repository_is_explained_not_raised(self):
        import urllib.error

        def fake(url, token="", accept=""):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertFalse(status.checked)
        self.assertFalse(status.available)
        self.assertIn("private", status.error)
        self.assertEqual(status.headline(), "Could not check for updates")

    def test_being_offline_is_explained_not_raised(self):
        import urllib.error

        def fake(url, token="", accept=""):
            raise urllib.error.URLError("no network")
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertIn("Could not reach GitHub", status.error)

    def test_a_matching_revision_is_not_an_update(self):
        def fake(url, token="", accept=""):
            return FakeResponse(json.dumps({"sha": "c4925f6aaaa"}).encode())
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        update.write_stamp(self.tmp, "c4925f6")
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.checked)
        self.assertFalse(status.available)
        self.assertEqual(status.headline(), "Up to date")

    def test_a_different_revision_is_an_update(self):
        def fake(url, token="", accept=""):
            return FakeResponse(json.dumps({"sha": "9999999bbbb"}).encode())
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        update.write_stamp(self.tmp, "c4925f6")
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.available)
        self.assertEqual(status.latest, "9999999")
        self.assertEqual(status.headline(), "Update available")


class TheLauncherShowsIt(unittest.TestCase):
    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent
                    / "courseforge" / "launcher.py").read_text(encoding="utf-8")

    def test_the_check_runs_on_a_thread(self):
        """A window that blocks on GitHub is a window that will not open."""
        self.assertIn("threading.Thread(target=self._check_update", self.src)

    def test_the_badge_is_hidden_until_there_is_news(self):
        self.assertIn("self.update_btn.pack(side=\"left\"", self.src)
        # Created, but not packed, at build time.
        build = self.src[self.src.index("def _build("):self.src.index("def _set_state(")]
        self.assertIn("self.update_btn = tk.Button(", build)
        self.assertNotIn("self.update_btn.pack(", build)

    def test_it_can_be_turned_off(self):
        self.assertIn('getattr(self.cfg, "check_updates", True)', self.src)

    def test_restarting_starts_a_new_process(self):
        """The running process imported the old code; only a new one is updated."""
        self.assertIn("subprocess.Popen", self.src)
        self.assertIn("courseforge.launcher", self.src)


if __name__ == "__main__":
    unittest.main()
