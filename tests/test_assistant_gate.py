"""The Assistant gate: Studio dry-run verbs are not grading-data access.

A brief that says "submission" or "grading criteria" is assignment prose.
extracted.json under data/<course>/<assignment>/ is a student record.
classify() is pure: no Canvas, no Claude, no network.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from courseforge.assistant import gate
from courseforge.assistant import routes as _assistant_routes  # noqa: F401
from courseforge.assistant.manager import Manager, Pending, auto_decision
from courseforge import routing

CWD = r"C:\courseforge\data\734975\workspace"

SCREENSHOT = (
    'python -m courseforge content draft --course 734975 --kind assignment '
    '--title "Underwater Basket Weaving" --points 20 --look clean '
    '--brief "Write a 20-point assignment on underwater basket weaving. '
    "Submission is an online text entry. Include simple grading criteria "
    'students can follow."'
)


def _bash(cmd):
    return gate.classify("Bash", {"command": cmd}, CWD)


def _read(path):
    return gate.classify("Read", {"file_path": path}, CWD)


class StudioDryRunsAreNotGradingData(unittest.TestCase):
    def test_the_screenshot_command_is_allowed(self):
        v = _bash(SCREENSHOT)
        self.assertEqual(v["decision"], "allow", v)
        self.assertNotEqual(v["kind"], "student-data", v)

    def test_a_graded_discussion_kind_is_content(self):
        v = _bash(
            'python -m courseforge content draft --course 734975 '
            '--kind "graded discussion" --title "Week 2" --points 10 '
            '--brief "Submission is a thread. Grading criteria: one reply."'
        )
        self.assertEqual(v["decision"], "allow", v)

    def test_other_dry_run_verbs_are_allowed(self):
        for cmd in (
            "python -m courseforge content place --course 734975 --draft x.json",
            "python -m courseforge content check-style --course 734975 --draft x.json",
            "python -m courseforge content check-quiz --course 734975 --draft x.json",
            "python -m courseforge course slo --course 734975 --program Networking",
            "python -m courseforge course due-dates --course 734975 --start 2026-08-17 --weeks 16",
            "python -m courseforge a11y dump --course 734975",
            "python -m courseforge pdf list --course 734975",
            "python -m courseforge students list --course 734975",
            "python -m courseforge students show --course 734975 --who Student-14",
        ):
            v = _bash(cmd)
            self.assertEqual(v["decision"], "allow", cmd)

    def test_workspace_files_whose_names_sound_like_grading_are_allowed(self):
        for path in (
            "workspace/syllabus-grading-scale.md",
            "workspace/assignment-submission-instructions.html",
            "grading-criteria.md",
            "draft.json",
        ):
            v = _read(path)
            self.assertEqual(v["decision"], "allow", path)

    def test_reading_the_content_draft_it_just_wrote_is_allowed(self):
        """The session cwd is workspace/; drafts land in the sibling build/."""
        v = _read(r"C:\courseforge\data\734975\build\drafts\20260915-123658-dc5321.json")
        self.assertEqual(v["decision"], "allow", v)
        self.assertNotEqual(v["kind"], "student-data", v)
        v = _read(r"..\build\drafts\20260915-123658-dc5321.json")
        self.assertEqual(v["decision"], "allow", v)


class RealGradebookFilesStillAsk(unittest.TestCase):
    def test_extracted_json_is_student_data(self):
        v = _read(r"data/734975/15977654/extracted.json")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)

    def test_names_json_is_student_data(self):
        v = _read(r"data/734975/names.json")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)

    def test_map_json_is_student_data(self):
        v = _read(r"data/734975/15977654/map.json")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)

    def test_cat_of_an_assignment_draft_is_student_data(self):
        v = _bash(r"cat data/734975/15977654/draft.json")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)

    def test_type_of_proposed_grades_is_student_data(self):
        v = _bash("type proposed-grades.json")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)

    def test_a_gradebook_csv_outside_the_studio_verbs_is_student_data(self):
        v = _bash(r"type C:\exports\gradebook.csv")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "student-data"), v)


class ApplyIsACanvasWriteNotStudentData(unittest.TestCase):
    def test_the_screenshot_command_with_apply_asks_to_write_canvas(self):
        v = _bash(SCREENSHOT + " --apply")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "canvas-write"), v)


class OtherAsksAreUnchanged(unittest.TestCase):
    def test_webfetch_off_canvas_asks(self):
        v = gate.classify("WebFetch", {"url": "https://example.com/x"}, CWD)
        self.assertEqual((v["decision"], v["kind"]), ("ask", "egress"), v)

    def test_serve_asks(self):
        v = _bash("python -m courseforge serve")
        self.assertEqual(v["decision"], "ask", v)
        self.assertNotEqual(v["kind"], "student-data", v)


class AssistantModes(unittest.TestCase):
    def test_auto_allows_local_reads_and_still_asks_for_canvas(self):
        self.assertEqual(auto_decision("auto", "read")[0], "allow")
        self.assertIsNone(auto_decision("auto", "canvas-write"))
        self.assertIsNone(auto_decision("auto", "student-data"))
        self.assertIsNone(auto_decision("auto", "run"))

    def test_plan_refuses_writes_and_allows_reads(self):
        self.assertEqual(auto_decision("plan", "canvas-write")[0], "deny")
        self.assertEqual(auto_decision("plan", "system")[0], "deny")
        self.assertEqual(auto_decision("plan", "read")[0], "allow")

    def test_ask_still_asks_about_outside_reads(self):
        self.assertIsNone(auto_decision("ask", "read"))
        self.assertIsNone(auto_decision("ask", "canvas-write"))

    def test_the_mode_route_is_registered(self):
        hit = routing.ROUTER.match("POST", "/api/assistant/734975/mode")
        self.assertIsNotNone(hit, "POST /api/assistant/{cid}/mode is missing from the router")
        hit = routing.ROUTER.match("POST", "/api/assistant/734975/prefs")
        self.assertIsNotNone(hit)
        hit = routing.ROUTER.match("POST", "/api/assistant/734975/sync")
        self.assertIsNotNone(hit, "POST /api/assistant/{cid}/sync is missing from the router")

    def test_auto_mode_settles_a_read_without_waiting(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = SimpleNamespace(data=str(tmp), port=8900, assistant_enabled=True,
                              assistant_model="", models=["opus", "sonnet", "haiku"],
                              base_url="https://x.instructure.com")

        class App:
            def __init__(self):
                self.cfg = cfg
                self.store = type("S", (), {"root": tmp, "courses": staticmethod(lambda: [])})()

            def course_dir(self, cid):
                p = Path(self.store.root) / str(cid)
                p.mkdir(parents=True, exist_ok=True)
                return p

        mgr = Manager(App(), port=8900)
        mgr.set_mode("734975", "auto")
        self.assertEqual(mgr.mode("734975"), "auto")
        out = mgr.permission_request({
            "secret": mgr.secret, "course": "734975",
            "tool_name": "Read", "kind": "read",
            "tool_input": {"file_path": r"C:\tmp\notes.md"},
            "summary": "Reading notes.md", "why": "outside the workspace",
            "timeout_s": 5,
        })
        self.assertEqual(out["decision"], "allow")
        self.assertFalse(mgr.pending_for("734975"))

        mgr.set_mode("734975", "plan")
        out = mgr.permission_request({
            "secret": mgr.secret, "course": "734975",
            "tool_name": "Bash", "kind": "canvas-write",
            "tool_input": {"command": "python -m courseforge content place --apply"},
            "summary": "place", "why": "changes Canvas",
            "timeout_s": 5,
        })
        self.assertEqual(out["decision"], "deny")
        self.assertIn("Plan mode", out["reason"])

        out = mgr.set_prefs("734975", model="sonnet")
        self.assertEqual(out["model"], "sonnet")
        self.assertEqual(mgr.chosen_model("734975"), "sonnet")
        self.assertEqual(mgr.state("734975")["models"], ["opus", "sonnet", "haiku"])


class TheNamesFooterFollowsTheKind(unittest.TestCase):
    def test_a_file_read_of_grading_data_warns_about_names(self):
        pending = Pending("r1", "734975", {
            "tool_name": "Read", "kind": "student-data",
            "tool_input": {"file_path": r"data/734975/15977654/extracted.json"},
            "summary": "Reading extracted.json", "why": "grading data",
        }, 30)
        self.assertTrue(pending.view()["leaks_names"])

    def test_a_studio_apply_card_does_not_claim_a_file_is_going_to_anthropic(self):
        pending = Pending("r2", "734975", {
            "tool_name": "Bash", "kind": "canvas-write",
            "tool_input": {"command": SCREENSHOT + " --apply"},
            "summary": "draft", "why": "changes the live Canvas course",
        }, 30)
        self.assertFalse(pending.view()["leaks_names"])


if __name__ == "__main__":
    unittest.main()
