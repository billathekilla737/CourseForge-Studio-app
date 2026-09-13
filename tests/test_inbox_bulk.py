"""Marking, archiving and deleting several inbox threads at once.

The property that carries this feature is that none of it can reach a student.
Marking read, archiving and deleting change the instructor's own copy of a
thread; Canvas never tells the other party, and there is no bulk form of the
one action that would. The second property is the ordinary one: it is a Canvas
write, so it is refused once and asks, and what it did is written down.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import audit, identity, inbox

ROSTER = [{"id": 101, "name": "Jordan Alvarez", "sortable_name": "Alvarez, Jordan"}]


def _thread(tid, subject, state="unread"):
    return {
        "id": tid, "subject": subject, "workflow_state": state,
        "message_count": 1, "context_code": "course_734975",
        "last_message_at": "2026-09-12T10:00:00Z",
        "last_message": "a message",
        "participants": [{"id": 101, "name": "Jordan Alvarez"},
                         {"id": 9, "name": "Zachary Garris"}],
        "messages": [{"id": 1, "author_id": 101,
                      "created_at": "2026-09-12T10:00:00Z", "body": "a message"}],
    }


THREADS = [_thread(77, "Project 2 deadline"), _thread(78, "Late work"),
           _thread(79, "Proctored test")]


class FakeClient:
    """Records every state change and deletion, and never grows a send path."""

    def __init__(self, fail_on=()):
        self.states = []
        self.deleted = []
        self.fail_on = {str(x) for x in fail_on}

    def conversations(self, scope="", course_id=None, limit=50):
        return list(THREADS)

    def conversation(self, cid, mark_read=False):
        return next(t for t in THREADS if str(t["id"]) == str(cid))

    def set_conversation_state(self, cid, state):
        if str(cid) in self.fail_on:
            raise RuntimeError("Canvas said no")
        self.states.append((str(cid), state))
        return {"id": cid}

    def delete_conversation(self, cid):
        if str(cid) in self.fail_on:
            raise RuntimeError("Canvas said no")
        self.deleted.append(str(cid))
        return {"id": cid}

    def students(self, cid):
        return ROSTER


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)

    def courses(self):
        return [{"id": "734975", "name": "202630 IST 2824 301 Intro", "title": "Intro"}]

    def assignments(self, cid):
        return []


class FakeApp:
    def __init__(self, root, fail_on=()):
        self.root = Path(root)
        self.client = FakeClient(fail_on)
        self.store = FakeStore(root)
        self.me_id = 9
        self.cfg = type("C", (), {"data": str(root), "pseudonymize": True,
                                  "describe_model": "sonnet"})()
        self.gated = []

    def course_dir(self, cid):
        p = self.root / str(cid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _gate(self, kind, payload, sentence, token, detail=None, what=""):
        self.gated.append({"kind": kind, "payload": payload, "sentence": sentence,
                           "detail": detail, "token": token})
        if not token:
            raise PermissionError(sentence)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        identity.forget()
        self.addCleanup(identity.forget)
        self.app = FakeApp(self.tmp)


class NoneOfItReachesAStudent(Base):
    def test_there_is_no_bulk_action_that_sends_anything(self):
        """The list of actions is the whole surface; a send would have to be
        added here, and it is not."""
        self.assertEqual(sorted(inbox.BULK_ACTIONS),
                         ["archive", "delete", "read", "unarchive", "unread"])

    def test_the_client_it_uses_has_no_send_in_this_path(self):
        inbox.bulk(self.app, [77, 78], "read", confirm_token="t")
        self.assertFalse(hasattr(self.app.client, "sent"))
        self.assertEqual(self.app.client.states, [("77", "read"), ("78", "read")])

    def test_deleting_says_the_student_keeps_their_copy(self):
        try:
            inbox.bulk(self.app, [77], "delete")
        except PermissionError:
            pass
        said = self.app.gated[0]["sentence"]
        self.assertIn("student keeps their copy", said)
        self.assertIn("cannot be undone", said)


class ItAsksBeforeItTouchesCanvas(Base):
    def test_the_first_pass_is_refused_and_nothing_changes(self):
        with self.assertRaises(PermissionError):
            inbox.bulk(self.app, [77, 78], "archive")
        self.assertEqual(self.app.client.states, [])
        self.assertEqual(self.app.client.deleted, [])

    def test_the_token_is_bound_to_the_threads_and_the_action(self):
        try:
            inbox.bulk(self.app, [78, 77], "delete")
        except PermissionError:
            pass
        payload = self.app.gated[0]["payload"]
        self.assertEqual(payload, {"action": "delete",
                                   "conversation_ids": ["77", "78"]})

    def test_the_confirmation_names_each_thread(self):
        try:
            inbox.bulk(self.app, [77, 79], "read")
        except PermissionError:
            pass
        detail = self.app.gated[0]["detail"]
        self.assertIsInstance(detail, str, "detail has to be JSON the dialog can parse")
        self.assertIn("Project 2 deadline", detail)
        self.assertIn("Proctored test", detail)
        self.assertNotIn("Late work", detail)

    def test_a_student_name_is_not_shown_as_the_thing_being_removed(self):
        """The dialog strikes the "from" value through. A sender's name there
        read as though the person, not the thread, were being deleted."""
        try:
            inbox.bulk(self.app, [77], "delete")
        except PermissionError:
            pass
        row = json.loads(self.app.gated[0]["detail"])[0]
        self.assertIn("Jordan Alvarez", row["label"])
        self.assertNotIn("Jordan", row["from"])
        self.assertEqual(row["from"], "unread")
        self.assertEqual(row["to"], "deleted")

    def test_an_unknown_action_is_refused_before_the_gate(self):
        with self.assertRaises(ValueError):
            inbox.bulk(self.app, [77], "send")
        self.assertEqual(self.app.gated, [])

    def test_an_empty_selection_is_refused(self):
        with self.assertRaises(ValueError):
            inbox.bulk(self.app, [], "read")

    def test_a_slip_of_the_hand_cannot_empty_the_inbox(self):
        with self.assertRaises(ValueError) as caught:
            inbox.bulk(self.app, list(range(inbox.MAX_BULK + 1)), "delete")
        self.assertIn(str(inbox.MAX_BULK), str(caught.exception))


class ItDoesWhatItSaidAndWritesItDown(Base):
    def test_marking_read_sets_the_state_on_each_one(self):
        out = inbox.bulk(self.app, [77, 78, 79], "read", confirm_token="t")
        self.assertEqual(self.app.client.states,
                         [("77", "read"), ("78", "read"), ("79", "read")])
        self.assertEqual(out["done"], ["77", "78", "79"])
        self.assertIn("Nothing was sent", out["sentence_done"])

    def test_unarchiving_puts_a_thread_back_as_read(self):
        """Canvas has no unarchive event: read is what returns it to the inbox."""
        inbox.bulk(self.app, [77], "unarchive", confirm_token="t")
        self.assertEqual(self.app.client.states, [("77", "read")])

    def test_deleting_calls_delete_and_not_a_state_change(self):
        inbox.bulk(self.app, [77, 78], "delete", confirm_token="t")
        self.assertEqual(self.app.client.deleted, ["77", "78"])
        self.assertEqual(self.app.client.states, [])

    def test_the_same_thread_twice_is_acted_on_once(self):
        inbox.bulk(self.app, [77, "77", 77], "read", confirm_token="t")
        self.assertEqual(self.app.client.states, [("77", "read")])

    def test_one_failure_does_not_stop_the_rest(self):
        app = FakeApp(self.tmp / "b", fail_on=["78"])
        out = inbox.bulk(app, [77, 78, 79], "delete", confirm_token="t")
        self.assertEqual(out["done"], ["77", "79"])
        self.assertEqual([f["id"] for f in out["failed"]], ["78"])
        self.assertIn("1 could not be changed", out["sentence_done"])

    def test_it_is_written_down_with_the_student_named(self):
        inbox.bulk(self.app, [77, 78], "delete", confirm_token="t")
        rows = audit.read(self.app.course_dir("734975"), limit=5)
        self.assertTrue(rows, "the deletion left no entry in the record")
        entry = rows[0]
        self.assertEqual(entry["area"], "inbox")
        self.assertEqual(entry["action"], "delete")
        self.assertIn("Deleted 2 conversations", entry["sentence"])
        self.assertIn("Jordan Alvarez",
                      [p.get("name") for p in (entry.get("students") or [])])

    def test_nothing_is_written_down_when_nothing_was_done(self):
        app = FakeApp(self.tmp / "c", fail_on=["77"])
        out = inbox.bulk(app, [77], "read", confirm_token="t")
        self.assertEqual(out["done"], [])
        self.assertEqual(audit.read(app.course_dir("734975"), limit=5), [])


if __name__ == "__main__":
    unittest.main()
