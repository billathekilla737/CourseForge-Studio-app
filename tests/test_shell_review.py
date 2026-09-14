"""Regressions from the shell review: the Assistant gate, the record, the
Reports job, and the shared front-end files.

Each class is one bug that was found and fixed. Nothing here touches the
network or starts a Claude session.
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from courseforge import audit, routing
from courseforge.assistant import gate

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "courseforge" / "web" / "js"
CWD = r"C:\data\734975\workspace"


def _bash(cmd):
    return gate.classify("Bash", {"command": cmd}, CWD)


class ShellReadsStayInsideTheWorkspace(unittest.TestCase):
    def test_get_content_of_names_json_is_a_question(self):
        v = _bash(r"Get-Content ..\names.json")
        self.assertEqual(v["decision"], "ask", v)

    def test_cat_of_a_workspace_file_is_still_allowed(self):
        v = _bash(r"Get-Content .\notes.md")
        self.assertEqual(v["decision"], "allow", v)


class GateAppliesThroughAVariable(unittest.TestCase):
    """--apply hidden in something the shell expands later. Every one of these
    reached the verb as a live Canvas write while the gate read a dry run."""

    def test_a_powershell_variable_is_a_question(self):
        v = _bash('$env:X = "--apply"; python -m courseforge a11y push --course 1 $env:X')
        self.assertEqual(v["decision"], "ask", v)

    def test_a_sub_expression_is_a_question(self):
        v = _bash("python -m courseforge a11y push --course 1 $(echo --apply)")
        self.assertEqual(v["decision"], "ask", v)

    def test_a_cmd_style_variable_is_a_question(self):
        v = _bash("python -m courseforge a11y push --course 1 %X%")
        self.assertEqual(v["decision"], "ask", v)

    def test_a_backtick_escape_is_a_question(self):
        v = _bash("python -m courseforge a11y push --course 1 `--apply`")
        self.assertEqual(v["decision"], "ask", v)

    def test_a_flag_split_by_quotes_is_still_the_flag(self):
        v = _bash("python -m courseforge a11y push --course 1 '--ap'ply")
        self.assertEqual(v["decision"], "ask", v)
        self.assertEqual(v["kind"], "canvas-write")

    def test_a_plain_quoted_title_still_runs_unasked(self):
        v = _bash('python -m courseforge content draft --course 1 --title "Week 3: Lighting"')
        self.assertEqual(v["decision"], "allow", v)

    def test_the_dry_run_and_the_plain_apply_are_unchanged(self):
        self.assertEqual(_bash("python -m courseforge a11y push --course 1")["decision"], "allow")
        v = _bash("python -m courseforge a11y push --course 1 --apply")
        self.assertEqual((v["decision"], v["kind"]), ("ask", "canvas-write"))


class ChainCatchesADeletedTail(unittest.TestCase):
    """Cutting the LAST line off a month file left nothing behind it to
    disagree, so verify() called the file unbroken. The bookmark in chain.json
    knows how many entries were written."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audit.forget_actor()
        audit.set_actor_source(lambda: {}, {})
        self.addCleanup(audit.forget_actor)
        for i in range(3):
            audit.record(self.tmp, "accommodations", "applied", f"Quiz {i}.",
                         students=[audit.person(900 + i, f"Student {i}")])
        self.file = audit.months(self.tmp)[0]

    def rows(self):
        return [json.loads(l) for l in self.file.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_deleting_the_last_entry_is_caught(self):
        rows = self.rows()[:-1]
        self.file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        out = audit.verify(self.tmp)
        self.assertFalse(out["ok"], out)
        self.assertIn("removed", out["why"])
        self.assertEqual(out["broke_at"]["seq"], 2)

    def test_a_rebuilt_tail_with_the_same_count_is_caught(self):
        rows = self.rows()
        rows[2]["sentence"] = "Quiz 2, honestly."
        rows[2]["hash"] = audit._digest(rows[2])           # re-chained, not merely edited
        self.file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        out = audit.verify(self.tmp)
        self.assertFalse(out["ok"], out)
        self.assertIn("end of the file was changed", out["why"])

    def test_a_bookmark_that_fell_behind_is_not_a_break(self):
        """_save_chain is best effort. A chain.json one entry behind the file
        means a write it missed, not a file somebody cut."""
        rows = self.rows()
        (audit.folder(self.tmp) / audit.CHAIN).write_text(
            json.dumps({"hash": rows[1]["hash"], "seq": 2}), encoding="utf-8")
        self.assertTrue(audit.verify(self.tmp)["ok"])

    def test_an_intact_file_still_verifies(self):
        self.assertTrue(audit.verify(self.tmp)["ok"])


class _App:
    def __init__(self, root):
        self.root = Path(root)
        self.store = type("S", (), {"root": self.root, "courses": staticmethod(lambda: [])})()
        self.cfg = type("C", (), {"audit_to_canvas": True})()

    def course_dir(self, cid):
        p = self.root / str(cid)
        p.mkdir(parents=True, exist_ok=True)
        return p


def _req(app, params, query=None, body=None):
    return routing.Request(app=app, handler=None, method="GET", path="",
                           params=params, query={k: [v] for k, v in (query or {}).items()},
                           body=body or {})


class RecordDownloadIsThisMonth(unittest.TestCase):
    """The button says "Download this month"; the route handed over the OLDEST
    month, because months() sorts ascending and the loop took the first."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = _App(self.tmp)
        folder = audit.folder(self.app.course_dir("734975"))
        folder.mkdir(parents=True)
        (folder / "2026-08.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "2026-09.jsonl").write_text("{}\n", encoding="utf-8")

    def test_no_month_means_the_newest(self):
        from courseforge.record import routes
        out = routes.download(_req(self.app, {"cid": "734975"}))
        self.assertEqual(Path(out.path).stem, "2026-09")

    def test_a_named_month_is_that_month(self):
        from courseforge.record import routes
        out = routes.download(_req(self.app, {"cid": "734975"}, {"month": "2026-08"}))
        self.assertEqual(Path(out.path).stem, "2026-08")

    def test_a_month_nobody_recorded_is_a_404(self):
        from courseforge.record import routes
        with self.assertRaises(routing.HTTPError) as cm:
            routes.download(_req(self.app, {"cid": "734975"}, {"month": "1999-01"}))
        self.assertEqual(cm.exception.status, 404)


class ReportsSurviveOneBadCourse(unittest.TestCase):
    """One course whose forecast raises took the whole score report down. It
    is now listed as failed, with the reason, and the rest still score."""

    def test_the_failing_course_is_named_and_the_rest_are_scored(self):
        from courseforge.reports import routes
        good = {"course_id": "1", "before": 60.0, "after": 90.0, "gain": 30.0,
                "files": 2, "needs_scan": False, "scan_kinds": [], "waiting": [],
                "method": "m", "method_note": "n"}

        def forecast(app, cid):
            if str(cid) == "2":
                raise RuntimeError("manifest unreadable")
            return dict(good, course_id=str(cid))

        app = _App(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, app.root, True)
        with mock.patch.object(routes.score, "forecast", forecast):
            job = routes.score_many(_req(app, {}, body={"course_ids": ["1", "2"]}))
            out = job.fn(lambda *_a, **_k: None)
        self.assertEqual([r["course_id"] for r in out["courses"]], ["1"])
        self.assertEqual(out["failed"][0]["course_id"], "2")
        self.assertIn("manifest unreadable", out["failed"][0]["error"])
        self.assertEqual(out["after"], 90.0)
        self.assertIn("1 course could not be read", out["sentence_done"])

    def test_the_page_prints_the_failed_list(self):
        src = (JS / "reports.js").read_text(encoding="utf-8")
        self.assertIn("failedHtml", src)
        self.assertIn("out.failed", src)


class ACourseWithNoSyllabusIsOneFinding(unittest.TestCase):
    """Seen live: a course with no syllabus counted as missing every one of
    the six statements, so "What to fix first" listed all six and the summary
    said "Most often missing: Disability services" about a course whose real
    problem was that there was nothing to read."""

    POLICIES = [{"id": "ada", "label": "Disability services", "why": "", "any": [r"disabilit"]},
                {"id": "grades", "label": "Grading scale", "why": "", "any": [r"grading scale"]}]

    def check_many(self):
        from courseforge import policies
        bodies = {"1": "<p>Disability services are available. Grading scale: A 90.</p>",
                  "2": "",
                  "3": "<p>Disability services are available.</p>"}

        class Content:
            def course_detail(self, cid, include=None):
                return {"name": "Course %s" % cid, "syllabus_body": bodies[str(cid)]}

        app = type("A", (), {"content": Content(), "cfg": type("C", (), {"base_url": "https://x"})()})()
        with mock.patch.object(policies, "load", return_value=self.POLICIES):
            return policies.check_many(app, ["1", "2", "3"])

    def test_the_empty_course_is_not_in_any_per_policy_count(self):
        out = self.check_many()
        by = {p["id"]: p for p in out["by_policy"]}
        self.assertEqual(by["ada"]["missing_in"], [])
        self.assertEqual(by["grades"]["missing_in"], ["Course 3"])
        self.assertEqual(by["ada"]["present"], 2)

    def test_it_is_its_own_line_and_the_summary_says_so(self):
        out = self.check_many()
        self.assertEqual(out["no_syllabus"], ["Course 2"])
        self.assertIn("Course 2 has no syllabus at all", out["summary"])
        self.assertIn("Most often missing: Grading scale (1 course)", out["summary"])
        self.assertNotIn("Disability services", out["summary"])

    def test_the_matrix_row_keeps_its_pill(self):
        out = self.check_many()
        row = next(r for r in out["courses"] if r["course_id"] == "2")
        self.assertTrue(row["empty"])
        src = (JS / "reports.js").read_text(encoding="utf-8")
        self.assertIn("no syllabus</span>", src)
        self.assertIn("out.no_syllabus", src)


class FrontEndContract(unittest.TestCase):
    """The rules in docs/FRONTEND-CONTRACT.md that a grep can hold."""

    FILES = ["assistant.js", "inbox.js", "record.js", "hub.js", "reports.js", "components.js"]

    def test_no_browser_confirm_or_alert(self):
        for name in self.FILES:
            src = (JS / name).read_text(encoding="utf-8")
            # `askConfirm(` and `runJobConfirmed(` are ours; a bare confirm( is the browser's.
            bare = re.findall(r"(?<![\w.])(?:confirm|alert)\s*\(", src)
            self.assertEqual(bare, [], f"{name} uses the browser dialog: {bare}")

    def test_no_location_reload_in_the_areas(self):
        for name in self.FILES:
            src = (JS / name).read_text(encoding="utf-8")
            self.assertNotIn("location.reload", src, name)

    def test_reports_do_not_take_all_terms_as_a_term_label(self):
        """grade.js stores '__all' for All terms; no course has that label, so
        the Reports picker came up with zero courses."""
        src = (JS / "reports.js").read_text(encoding="utf-8")
        self.assertIn("S.term !== '__all'", src)

    def test_the_inbox_guards_on_the_route_not_the_shared_view(self):
        src = (JS / "inbox.js").read_text(encoding="utf-8")
        self.assertIn("S.route.parts[0] === 'inbox'", src)
        self.assertNotIn("if (S.view !== 'inbox') return;", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
