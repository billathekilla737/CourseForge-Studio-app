"""The Assistant's end of the name swap: what goes out, and what comes back.

Nothing here starts a Claude session. The manager is driven directly, which is
the only honest way to assert what `send` hands to the session object.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from courseforge import identity
from courseforge.assistant import manager as M

ROSTER = [
    {"id": 101, "name": "Jordan Alvarez", "sortable_name": "Alvarez, Jordan"},
    {"id": 205, "name": "Dana Wu", "sortable_name": "Wu, Dana"},
]


class FakeSession:
    def __init__(self):
        self.sent = []
        self.session_id = "sess-1"

    def alive(self):
        return True

    def send(self, text):
        self.sent.append(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        identity.forget()
        self.addCleanup(identity.forget)

        cfg = type("Cfg", (), {"data": str(self.tmp), "port": 8900,
                               "pseudonymize": True, "assistant_enabled": True,
                               "base_url": "https://x.instructure.com"})()

        class App:
            def __init__(self, tmp):
                self.cfg = cfg
                self.store = type("S", (), {"root": tmp, "courses": staticmethod(lambda: [])})()

            def course_dir(self, cid):
                p = Path(self.store.root) / str(cid)
                p.mkdir(parents=True, exist_ok=True)
                return p

            client = type("C", (), {"students": staticmethod(lambda cid: ROSTER)})()

        self.mgr = M.Manager(App(self.tmp), port=8900)
        self.c = self.mgr.course("734975")
        self.session = FakeSession()
        self.c.session = self.session


class Outbound(Base):
    def test_the_real_name_never_reaches_the_session(self):
        out = self.mgr.send("734975", "Why is Jordan Alvarez behind?")
        self.assertEqual(self.session.sent, ["Why is Student-1 behind?"])
        self.assertEqual([s["tag"] for s in out["swapped"]], ["Student-1"])

    def test_what_is_kept_is_what_was_sent(self):
        """The transcript on disk is the record of what left this machine, so
        it holds the tag. The page is where the name comes back."""
        self.mgr.send("734975", "Email Dana Wu today.")
        stored = [e for e in self.c.ring if e["kind"] == "user"][-1]
        self.assertEqual(stored["text"], "Email Student-2 today.")
        shown = self.mgr.events("734975", 0)["events"]
        self.assertIn("Email Dana Wu today.",
                      [e.get("text") for e in shown if e["kind"] == "user"])

    def test_an_ambiguous_name_stops_the_message(self):
        with mock.patch.object(self.mgr.app.client, "students", staticmethod(lambda cid: [
                {"id": 1, "name": "Chris Okafor", "sortable_name": "Okafor, Chris"},
                {"id": 2, "name": "Robin Okafor", "sortable_name": "Okafor, Robin"}])):
            identity.forget()
            with self.assertRaises(ValueError) as caught:
                self.mgr.send("734975", "Did Okafor submit?")
        self.assertIn("Chris Okafor", str(caught.exception))
        self.assertEqual(self.session.sent, [], "it sent the message anyway")


class Inbound(Base):
    """Claude streams a few characters at a time, and a tag lands split."""

    def feed(self, *chunks):
        for text in chunks:
            self.mgr._on_event(self.c, {"kind": "text", "text": text})
        self.mgr._on_event(self.c, {"kind": "text_end"})
        return "".join(e.get("text") or "" for e in self.mgr.events("734975", 0)["events"]
                       if e["kind"] == "text")

    def test_a_tag_split_across_chunks_still_comes_back_as_a_name(self):
        self.mgr.send("734975", "hello")
        self.assertEqual(self.feed("Stud", "ent-", "1 is behind."),
                         "Jordan Alvarez is behind.")

    def test_a_tag_split_letter_by_letter(self):
        self.mgr.send("734975", "hello")
        self.assertEqual(self.feed(*"Student-2 is fine."), "Dana Wu is fine.")

    def test_nothing_is_lost_when_a_turn_ends_mid_tag(self):
        """The last thing written was "Stud" and no more is coming. It has to
        be shown, not swallowed."""
        self.mgr.send("734975", "hello")
        self.assertEqual(self.feed("Ask ", "Stud"), "Ask Stud")

    def test_ordinary_prose_is_not_delayed_into_the_wrong_order(self):
        self.mgr.send("734975", "hello")
        self.assertEqual(self.feed("Four ", "pages ", "were ", "restyled."),
                         "Four pages were restyled.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
