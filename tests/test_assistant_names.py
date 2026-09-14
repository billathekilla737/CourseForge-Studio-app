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

    def test_a_misspelt_name_stops_the_message_and_offers_the_fix(self):
        with self.assertRaises(M.NameProblem) as caught:
            self.mgr.send("734975", "Is Jordam caught up?")
        view = caught.exception.view()
        self.assertEqual(self.session.sent, [], "it sent the typo anyway")
        self.assertEqual([n["suggestion"] for n in view["near"]], ["Jordan Alvarez"])
        self.assertTrue(view["can_send_anyway"],
                        "the page has no way past a checker that is wrong")

    def test_but_it_can_be_overridden(self):
        """The near-miss check is the one that can be wrong about an ordinary
        word, so there has to be a way past it."""
        self.mgr.send("734975", "Is Jordam caught up?", allow_near=True)
        self.assertEqual(self.session.sent, ["Is Jordam caught up?"])

    def test_an_ambiguous_name_offers_no_override(self):
        """There is no safe way to resolve it here: sending leaks a real
        surname and picking one answers about the wrong student."""
        with mock.patch.object(self.mgr.app.client, "students", staticmethod(lambda cid: [
                {"id": 1, "name": "Chris Okafor", "sortable_name": "Okafor, Chris"},
                {"id": 2, "name": "Robin Okafor", "sortable_name": "Okafor, Robin"}])):
            identity.forget()
            with self.assertRaises(M.NameProblem) as caught:
                self.mgr.send("734975", "Did Okafor submit?", allow_near=True)
        self.assertFalse(caught.exception.view()["can_send_anyway"])
        self.assertEqual(self.session.sent, [])

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


class AWarningIsNotTheLimit(unittest.TestCase):
    """Claude CLI sends rate_limit_event on every turn. allowed_warning means
    the request ran; the page used to say the limit was reached anyway."""

    def notice(self, status, resets=None):
        from courseforge.assistant.session import rate_limit_notice
        info = {"status": status}
        if resets is not None:
            info["resetsAt"] = resets
        return rate_limit_notice(info)

    def test_allowed_is_silent(self):
        self.assertIsNone(self.notice("allowed"))

    def test_allowed_warning_is_silent(self):
        self.assertIsNone(self.notice("allowed_warning", resets=0))

    def test_rejected_says_the_limit_was_reached(self):
        text = self.notice("rejected")
        self.assertIn("usage limit has been reached", text)
        import time
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        timed = self.notice("rejected", resets=midnight)
        self.assertIn("12:00 AM", timed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
