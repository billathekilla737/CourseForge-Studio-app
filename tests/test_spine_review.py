"""Safety-spine review tests (2026-09-13). unittest, no network.

Two kinds of test live here:

* plain tests pin down behaviour the spine already gets right (the confirm
  gate's binding, replay and expiry rules; the policy's deny list; the
  same-origin check on POST);
* every gap found in the review below has since been closed; each test here is the
  review. They pass today *because* the gap exists. When a gap is fixed the
  test flips to "unexpected success", which is the signal to drop the marker.
"""
from __future__ import annotations

import email.message
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from courseforge import canvas_policy as cp  # noqa: E402
from courseforge import claude_cli, confirm  # noqa: E402
from courseforge.store import Store  # noqa: E402

BASE = "https://x.instructure.com"


# ------------------------------------------------------------------ confirm
class ConfirmGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = confirm.ConfirmGate()
        self.payload = {"course_id": "1", "grades": [["7", 9.5]]}

    def _offer(self, kind="push", payload=None):
        with self.assertRaises(confirm.ConfirmRequired) as cm:
            self.gate.require(kind, payload or self.payload, "write it", token=None)
        self.assertTrue(cm.exception.payload()["needs_confirm"])
        return cm.exception.token

    def test_token_spends_once_on_the_same_payload(self):
        tok = self._offer()
        self.gate.require("push", self.payload, "write it", token=tok)     # spends
        with self.assertRaises(confirm.ConfirmStale):
            self.gate.require("push", self.payload, "write it", token=tok)  # replay

    def test_token_for_payload_a_cannot_spend_on_payload_b(self):
        tok = self._offer()
        other = {"course_id": "1", "grades": [["7", 10.0]]}
        with self.assertRaises(confirm.ConfirmStale):
            self.gate.require("push", other, "write it", token=tok)
        # Spent by the failed attempt: it cannot then be used for the right one.
        with self.assertRaises(confirm.ConfirmStale):
            self.gate.require("push", self.payload, "write it", token=tok)

    def test_token_is_bound_to_kind(self):
        tok = self._offer("push")
        with self.assertRaises(confirm.ConfirmStale):
            self.gate.require("release", self.payload, "x", token=tok)

    def test_key_order_does_not_change_the_fingerprint(self):
        tok = self._offer("push", {"a": 1, "b": [1, 2]})
        self.gate.require("push", {"b": [1, 2], "a": 1}, "x", token=tok)

    def test_expired_token_is_refused(self):
        gate = confirm.ConfirmGate(ttl_s=1)
        with self.assertRaises(confirm.ConfirmRequired) as cm:
            gate.require("push", self.payload, "x", None)
        tok = cm.exception.token
        # Age the entry instead of sleeping.
        gate._pending[tok].created -= 5
        with self.assertRaises(confirm.ConfirmStale):
            gate.require("push", self.payload, "x", token=tok)
        self.assertEqual(gate.pending_count(), 0)

    def test_unknown_and_empty_tokens_are_refused_or_reoffered(self):
        with self.assertRaises(confirm.ConfirmStale):
            self.gate.require("push", self.payload, "x", token="deadbeef")
        with self.assertRaises(confirm.ConfirmRequired):
            self.gate.require("push", self.payload, "x", token="")

    def test_concurrent_spend_lets_exactly_one_through(self):
        tok = self._offer()
        results: list[str] = []
        start = threading.Barrier(8)

        def worker():
            start.wait()
            try:
                self.gate.require("push", self.payload, "x", token=tok)
                results.append("ok")
            except confirm.ConfirmStale:
                results.append("stale")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("stale"), 7)

    def test_pending_is_bounded(self):
        for i in range(confirm.ConfirmGate.MAX_PENDING + 10):
            self.gate.offer("push", {"i": i}, "x")
        self.assertLessEqual(self.gate.pending_count(), confirm.ConfirmGate.MAX_PENDING)


# ------------------------------------------------------------------- policy
def _content_ok(path: str) -> bool:
    url = f"{BASE}/api/v1{path}" if not path.startswith("/api/graphql") else BASE + path
    try:
        cp.check_scope("content", "GET", url)
        return True
    except cp.PolicyDenied:
        return False


class PolicyDeniesTests(unittest.TestCase):
    DENIED = [
        "/courses/1/users", "/courses/1/users?enrollment_type[]=student",
        "/courses/1/enrollments", "/courses/1/students", "/courses/1/search_users",
        "/courses/1/recent_students", "/courses/1/assignments/2/submissions",
        "/courses/1/assignments/2/submission_summary", "/courses/1/gradebook_history/days",
        "/courses/1/analytics/activity", "/courses/1/analytics/student_summaries",
        "/conversations", "/conversations/5", "/search/recipients",
        "/courses/1/quizzes/2/submissions", "/quiz_submissions/9/events",
        "/courses/1/discussion_topics/2/entries", "/groups/5/users",
        "/sections/9", "/sections/9?include[]=students", "/courses/1/late_policy",
        "/courses/1/assignments/2/peer_reviews", "/courses/1/outcome_results",
        "/users/7/profile", "/accounts/1/users", "/api/graphql",
    ]

    def test_student_data_endpoints_are_denied(self):
        for path in self.DENIED:
            with self.subTest(path=path):
                self.assertFalse(_content_ok(path), f"{path} should be refused")

    def test_grading_and_full_scopes_are_not_restricted(self):
        cp.check_scope("grading", "GET", f"{BASE}/api/v1/courses/1/users")
        cp.check_scope("full", "POST", f"{BASE}/api/graphql")

    def test_token_only_travels_to_the_canvas_host_over_https(self):
        with self.assertRaises(cp.PolicyDenied):
            cp.assert_token_host(BASE, "http://x.instructure.com/api/v1/courses")
        with self.assertRaises(cp.PolicyDenied):
            cp.assert_token_host(BASE, "https://evil.example/api/v1/courses")
        cp.assert_token_host(BASE, "https://x.instructure.com/api/v1/courses")
        cp.assert_token_host(BASE, "https://y.instructure.com/x", ["*.instructure.com"])


class PolicyGapsTests(unittest.TestCase):
    """Each of these is a URL that returns or lists people and that the content
    scope currently lets through. Expected to fail until canvas_policy is
    tightened (see the review: include[] is never inspected, _ALLOW matches any
    /courses/ segment, and the path is not percent-decoded)."""

    def test_include_students_on_course_sections(self):
        self.assertFalse(_content_ok("/courses/1/sections?include[]=students"))

    def test_include_students_on_one_section(self):
        self.assertFalse(_content_ok("/courses/1/sections/9?include[]=students"))

    def test_discussion_view_lists_student_posts(self):
        self.assertFalse(_content_ok("/courses/1/discussion_topics/2/view"))

    def test_gradeable_students(self):
        self.assertFalse(_content_ok("/courses/1/assignments/2/gradeable_students"))

    def test_quiz_reports_are_per_student(self):
        self.assertFalse(_content_ok("/courses/1/quizzes/2/reports"))

    def test_rubric_assessments_via_include(self):
        self.assertFalse(_content_ok("/courses/1/rubrics/3?include[]=assessments&style=full"))

    def test_percent_encoded_segment_bypasses_deny(self):
        self.assertFalse(_content_ok("/courses/1/%75sers"))

    def test_own_activity_stream_carries_student_names(self):
        self.assertFalse(_content_ok("/users/self/activity_stream"))

    def test_account_reports(self):
        self.assertFalse(_content_ok("/accounts/1/reports/grade_export_csv"))

    def test_file_listing_with_uploader_include_is_allowed(self):
        """include[]=user on a file is the uploader, not a roster."""
        self.assertTrue(_content_ok("/courses/1/files?include[]=user"))
        self.assertTrue(_content_ok("/courses/1/files"))

    def test_file_listing_with_users_include_is_still_refused(self):
        self.assertFalse(_content_ok("/courses/1/files?include[]=users"))

    def test_file_download_leaf_is_allowed(self):
        self.assertTrue(_content_ok("/files/123/download"))


# ------------------------------------------------------------------- server
class SameOriginTests(unittest.TestCase):
    def _handler(self):
        from courseforge import server

        class Cfg:
            port = 8900

        class FakeApp:
            cfg = Cfg()

        handler_cls = server.make_handler(FakeApp())
        h = handler_cls.__new__(handler_cls)
        h.headers = email.message.Message()
        return h

    def test_cross_origin_post_is_refused(self):
        h = self._handler()
        h.headers["Origin"] = "https://evil.example"
        h.headers["Content-Type"] = "application/json"
        self.assertFalse(h._same_origin())

    def test_null_origin_is_refused(self):
        h = self._handler()
        h.headers["Origin"] = "null"
        h.headers["Content-Type"] = "application/json"
        self.assertFalse(h._same_origin())

    def test_form_post_without_json_is_refused(self):
        h = self._handler()
        h.headers["Origin"] = "http://127.0.0.1:8900"
        h.headers["Content-Type"] = "application/x-www-form-urlencoded"
        self.assertFalse(h._same_origin())

    def test_own_page_and_local_scripts_pass(self):
        h = self._handler()
        h.headers["Origin"] = "http://localhost:8900"
        h.headers["Content-Type"] = "application/json; charset=utf-8"
        self.assertTrue(h._same_origin())
        h2 = self._handler()                       # curl: no Origin at all
        h2.headers["Content-Type"] = "text/plain"
        self.assertTrue(h2._same_origin())


class StoreTraversalTests(unittest.TestCase):
    def test_assignment_dir_stays_under_the_data_root(self):
        """`/api/a/<cid>/<aid>/file` feeds raw path segments into
        Store.assignment_dir, which mkdirs root/<cid>/<aid>/files. A `..`
        segment (curl --path-as-is) lands outside data/."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            store = Store(root)
            for cid, aid in (("..", "x"), ("1", ".."), ("..\\..", "y"), ("a/b", "1")):
                with self.assertRaises(ValueError, msg=f"{cid!r}/{aid!r}"):
                    store.assignment_dir(cid, aid)
            self.assertFalse((root / "x").exists())
            self.assertEqual(store.assignment_dir("1", "2"), root / "1" / "2")
            self.assertEqual(store.course_dir("account"), root / "account")


# --------------------------------------------------------------- claude seam
class ClaudeCliTests(unittest.TestCase):
    def _capture_cmd(self, **kwargs):
        seen = {}

        class FakeProc:
            args = []
            returncode = 0

            def __init__(self, cmd, **kw):
                seen["cmd"] = cmd
                seen["env"] = kw.get("env") or {}

            def communicate(self, stdin_text=None, timeout=None):
                seen["stdin"] = stdin_text
                return json.dumps({"type": "result", "result": "{\"ok\": true}",
                                   "is_error": False, "total_cost_usd": 0.0}), ""

        old_popen, old_path = claude_cli.subprocess.Popen, claude_cli.cli_path
        claude_cli.subprocess.Popen = FakeProc
        claude_cli.cli_path = lambda: "claude"
        try:
            claude_cli.run("hello prompt", model="sonnet", **kwargs)
        finally:
            claude_cli.subprocess.Popen, claude_cli.cli_path = old_popen, old_path
        return seen

    def test_prompt_travels_on_stdin_not_argv(self):
        seen = self._capture_cmd()
        self.assertEqual(seen["stdin"], "hello prompt")
        self.assertNotIn("hello prompt", " ".join(seen["cmd"]))

    def test_strip_env_removes_nested_session_variables(self):
        import os
        old = dict(os.environ)
        try:
            os.environ["ANTHROPIC_BASE_URL"] = "http://proxy"
            os.environ["CLAUDECODE"] = "1"
            env = claude_cli.child_env()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("CLAUDECODE", env)

    def test_child_env_does_not_carry_the_canvas_token(self):
        import os
        old = dict(os.environ)
        try:
            os.environ["CANVAS_TOKEN"] = "not-a-real-token-value"
            env = claude_cli.child_env()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("CANVAS_TOKEN", env)

    def test_grading_call_disables_built_in_tools(self):
        """`claude -p` keeps Read/Glob/Grep available by default; a grading
        prompt built from a student file could ask the model to read
        data/<course>/<assignment>/map.json. The call should pass --tools ""
        (or --disallowedTools) so no tool is reachable."""
        cmd = self._capture_cmd()["cmd"]
        self.assertTrue("--tools" in cmd or "--disallowedTools" in cmd, cmd)

    def test_stream_json_parser_tolerates_partial_and_junk_lines(self):
        junk = "garbage\n{\"type\": \"system\", \"subtype\":\n{\"type\": \"result\", \"result\": \"hi\", \"is_error\": false}\n"
        env = claude_cli._envelope(junk)
        self.assertEqual(env["result"], "hi")
        seen: list[dict] = []
        act = claude_cli._Activity(seen.append)
        act.feed("{not json")
        act.feed("")
        act.feed(json.dumps({"type": "stream_event", "event": {"type": "message_start"}}))
        self.assertEqual(seen[-1]["phase"], "responding")

    def test_not_logged_in_detection(self):
        seen = {}

        class FakeProc:
            args = []
            returncode = 1

            def __init__(self, cmd, **kw):
                pass

            def communicate(self, stdin_text=None, timeout=None):
                return "", "Not logged in. Please run /login"

        old_popen, old_path = claude_cli.subprocess.Popen, claude_cli.cli_path
        claude_cli.subprocess.Popen = FakeProc
        claude_cli.cli_path = lambda: "claude"
        try:
            with self.assertRaises(claude_cli.NotLoggedIn):
                claude_cli.run("x", model="sonnet")
        finally:
            claude_cli.subprocess.Popen, claude_cli.cli_path = old_popen, old_path


# ---------------------------------------------------------------- token store
@unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
class TokenStoreTests(unittest.TestCase):
    def test_dpapi_round_trip_and_plaintext_removed(self):
        from courseforge import secrets as ts
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / ts.PLAIN_NAME).write_text("old-plaintext-copy-abcdefghijklmnop\n")
            path = ts.write_token(d, "  \"7~abcdefghijklmnopqrstuvwxyz0123456789ABCDEF\"  ")
            self.assertEqual(path.name, ts.ENC_NAME)
            self.assertFalse((d / ts.PLAIN_NAME).exists())
            self.assertEqual(ts.read_token_file(path), "7~abcdefghijklmnopqrstuvwxyz0123456789ABCDEF")
            # The blob on disk is hex, never the token.
            self.assertNotIn("abcdefghijklmnop", path.read_text())

    def test_implausible_token_is_refused(self):
        from courseforge import secrets as ts
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ts.TokenError):
                ts.write_token(Path(tmp), "short")
            with self.assertRaises(ts.TokenError):
                ts.write_token(Path(tmp), "https://not.a.token/" + "x" * 30)


# ------------------------------------------------------------------- launch
class LaunchFilesTests(unittest.TestCase):
    def test_run_cmd_starts_the_renamed_package(self):
        text = (ROOT / "run.cmd").read_text(encoding="utf-8", errors="replace")
        self.assertIn("python -m courseforge", text)
        self.assertNotIn("canvasgrader", text)

    def test_vbs_launches_the_launcher_module(self):
        text = (ROOT / "CourseForge Studio.vbs").read_text(encoding="utf-8", errors="replace")
        self.assertIn("-m courseforge.launcher", text)
        self.assertIn("config.example.json", text)
        self.assertIn("courseforge gui", text)

    def test_not_logged_in_accepts_a_message(self):
        err = claude_cli.NotLoggedIn("ANTHROPIC_API_KEY is not set.")
        self.assertIn("ANTHROPIC_API_KEY", str(err))

    def test_packaged_version_matches_pyproject(self):
        import re as _re
        from courseforge import __version__
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = _re.search(r'(?m)^version = "([^"]+)"', text)
        self.assertEqual(__version__, m.group(1))

    def test_persist_writes_audit_to_canvas(self):
        from courseforge.config import Config
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{}", encoding="utf-8")
            cfg = Config.load(path)
            cfg.persist(audit_to_canvas=False)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(raw["audit_to_canvas"])
            cfg2 = Config.load(path)
            self.assertFalse(cfg2.audit_to_canvas)

    def test_launcher_can_build_a_restart_argv(self):
        import inspect
        from courseforge import launcher
        self.assertTrue(hasattr(launcher, "Path"))
        src = inspect.getsource(launcher.Launcher.restart)
        self.assertIn("Path(exe)", src)


if __name__ == "__main__":
    unittest.main()
