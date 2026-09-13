"""Installing veraPDF unattended: the script it writes, and what it refuses.

Nothing here downloads anything or starts a JVM. The two things worth pinning
are the ones that actually went wrong: the unattended-install script has to
name the installer's real panels, and a download that does not match the hash
must stop the install rather than feed an unknown archive to Java.
"""
import hashlib
import io
import unittest
from pathlib import Path
from unittest import mock

from courseforge import verapdf_setup as V


class AutoInstallScript(unittest.TestCase):
    def setUp(self):
        self.xml = V.auto_xml(Path(r"C:\somewhere\veraPDF"))

    def test_every_panel_of_the_wizard_is_answered(self):
        """A script that misses a screen is where the installer stops and says
        the automated installation failed."""
        for klass, panel_id in V.PANELS:
            self.assertIn(klass, self.xml, f"{klass} is not in the script")
            self.assertIn(f'id="{panel_id}"', self.xml)

    def test_it_says_where_to_install(self):
        self.assertIn("<installpath>C:\\somewhere\\veraPDF</installpath>", self.xml)

    def test_the_command_line_validator_is_selected(self):
        """The Studio shells out to the CLI. The GUI pack rides along because
        it carries shared jars the CLI wrapper loads."""
        self.assertIn('name="veraPDF CLI" selected="true"', self.xml)
        self.assertIn('name="veraPDF GUI" selected="true"', self.xml)
        self.assertIn('name="veraPDF Sample Plugins" selected="false"', self.xml)

    def test_it_is_parseable_xml(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.xml)
        self.assertEqual(root.tag, "AutomatedInstallation")
        self.assertEqual(len(list(root)), len(V.PANELS))


class StaleLock(unittest.TestCase):
    """IzPack takes a single-instance lock and an interrupted run leaves it.

    Every later install then refuses with "already running" and prints
    "[ Automated installation FAILED! ]", which reads like a rejected script
    and is not. Clearing it is what the installer's own message tells a person
    to do -- but only when it is empty, because a lock with a port in it may
    belong to an installer that really is open.
    """

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        p = mock.patch.object(V.tempfile, "gettempdir", lambda: str(self.tmp))
        p.start()
        self.addCleanup(p.stop)

    def test_an_empty_lock_is_cleared(self):
        (self.tmp / V.LOCK).write_bytes(b"")
        said = []
        self.assertTrue(V.clear_stale_lock(said.append))
        self.assertFalse((self.tmp / V.LOCK).exists())
        self.assertTrue(any("lock file" in s for s in said), said)

    def test_a_lock_with_something_in_it_is_left_alone(self):
        (self.tmp / V.LOCK).write_bytes(b"51234")
        said = []
        self.assertFalse(V.clear_stale_lock(said.append))
        self.assertTrue((self.tmp / V.LOCK).exists(), "it deleted a live lock")
        self.assertTrue(any("Close any open" in s for s in said), said)

    def test_no_lock_is_not_a_problem(self):
        self.assertFalse(V.clear_stale_lock())


class HashGate(unittest.TestCase):
    """Nothing that fails the hash check is ever handed to Java."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def _serve(self, payload: bytes):
        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_a): return False
        return mock.patch("urllib.request.urlopen", lambda *a, **k: Resp(payload))

    def test_a_tampered_download_is_refused_and_deleted(self):
        with self._serve(b"not the installer"):
            with self.assertRaises(RuntimeError) as caught:
                V._download(self.tmp, lambda _t: None)
        self.assertIn("did not match the hash", str(caught.exception))
        self.assertEqual(list(self.tmp.iterdir()), [], "the bad download was kept")

    def test_the_real_hash_is_accepted(self):
        payload = b"pretend installer"
        with self._serve(payload), \
             mock.patch.object(V, "SHA256", hashlib.sha256(payload).hexdigest()):
            path = V._download(self.tmp, lambda _t: None)
        self.assertEqual(path.read_bytes(), payload)

    def test_install_stops_before_java_when_there_is_no_java(self):
        from courseforge import tools
        ran = []
        with mock.patch.object(tools, "detect",
                               lambda c=None: {"java": {"ok": False, "path": ""}}), \
             mock.patch.object(V.subprocess, "Popen", lambda *a, **k: ran.append(a)):
            out = V.install(None, on_line=lambda _t: None)
        self.assertFalse(out["ok"])
        self.assertIn("no Java here yet", out["detail"])
        self.assertEqual(ran, [])


class WhereItLooks(unittest.TestCase):
    def test_the_per_user_folder_needs_no_administrator(self):
        """Program Files would, and asking an instructor on a managed laptop
        for administrator rights is how this ends up not happening at all."""
        self.assertNotIn("Program Files", str(V.default_dir()))
        self.assertIn(str(V.default_dir()), [str(p) for p in V.search_dirs()])

    def test_find_returns_empty_when_nothing_is_installed(self):
        with mock.patch.object(V, "search_dirs", lambda: [Path("/nope/nowhere")]):
            self.assertEqual(V.find(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
