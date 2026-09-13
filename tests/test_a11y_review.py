"""Regression tests from the Accessibility area review. Standard library only.

Run:  python -m unittest tests.test_a11y_review

Nothing here touches a network. Each test pins one thing that was wrong on the
page or in the numbers, and says in its name what that was:

  - the HTML dry run carries the plan where the gateway component reads it
  - every confirm gate is handed its detail as a JSON string, the shape the
    confirm dialog parses (a list printed "[object Object]" per file)
  - a batch file upload lands in the course ledger like a per-course one
  - the file compliance survey reads the words the PDF state actually uses
  - the Pages score reads the verify report as the list it is
  - the file compliance screen does not use the browser's confirm()
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from courseforge import ledger, routing, score  # noqa: E402
from courseforge.a11y import batch_files, dump, restyle, routes as a11y_routes  # noqa: E402
from courseforge.a11y.workdir import workdir  # noqa: E402
from courseforge.docs import gateway  # noqa: E402
from test_a11y import FakeApp, FakeClient, course_items  # noqa: E402

WEB = Path(HERE).parent / "courseforge" / "web"


class Stop(Exception):
    """Raised by a fake gate so a push stops at the doorstep, as the real
    first attempt does."""


class ReviewBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cf-review-"))
        self.client = FakeClient(course_items())
        self.app = FakeApp(self.root, self.client)
        self.cid = "999"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ------------------------------------------------------- HTML dry run shape

class HtmlDryRunCarriesThePlan(ReviewBase):
    def test_dry_run_answers_with_planned_and_skipped_lists(self):
        """components.js reads `planned` and `skipped` off the top of the dry
        run answer. Nested inside `plan` they were invisible, the Dry run step
        read "Nothing is planned" and Apply stayed disabled."""
        wd = workdir(self.app.course_dir(self.cid))
        dump.dump(self.client, self.cid, wd, course_label="Test Course", base_url="https://canvas.test")
        restyle.transform(wd, "clean")
        restyle.verify(wd)
        req = routing.Request(app=self.app, handler=None, method="POST",
                              path="/api/a11y/999/html/push", params={"cid": self.cid},
                              body={"apply": False, "keys": [], "kind": "html"})
        out = a11y_routes.push_(req)
        self.assertTrue(out["dry_run"])
        self.assertIsInstance(out["planned"], list)
        self.assertIsInstance(out["skipped"], list)
        self.assertEqual(len(out["planned"]), out["plan"]["count"])
        self.assertGreater(len(out["planned"]), 0)
        for row in out["planned"]:
            for key in ("key", "title", "label", "from", "to"):
                self.assertIn(key, row)
        self.assertEqual(self.client.writes, [], "a dry run must not write")


# ------------------------------------------------------- confirm detail shape

class ConfirmDetailIsAString(ReviewBase):
    """askConfirm in core.js does JSON.parse(info.detail). A Python list goes
    over the wire as an array, JSON.parse of an array throws, and the dialog
    falls back to printing the array itself: "[object Object],[object Object]".
    course_pdfs already sends a string; these two paths did not."""

    def _plan(self):
        return {"kind": "pptx", "course_id": self.cid, "count": 1, "blocked": [],
                "rows": [], "sentence": "Upload 1 fixed PowerPoint file.",
                "fingerprint": {"course_id": self.cid},
                "uploads": [{"file_id": "5", "name": "Week 1.pptx", "folder": "", "folder_id": 1,
                             "from": 1000, "to": 1100, "changes": ["2 alt texts"],
                             "already_pushed": False}]}

    def test_docs_push_gate_gets_json_text(self):
        seen = {}

        def gate(payload, sentence, detail):
            seen["detail"] = detail
            raise Stop()

        with mock.patch.object(gateway, "plan", return_value=self._plan()):
            with self.assertRaises(Stop):
                gateway.push(self.app, self.cid, gateway.KINDS["pptx"], apply=True, gate=gate)
        self.assertIsInstance(seen["detail"], str)
        rows = json.loads(seen["detail"])
        self.assertEqual(rows[0]["label"], "Week 1.pptx")
        self.assertEqual(self.client.writes, [])

    def test_docs_restore_gate_gets_json_text(self):
        seen = {}

        def gate(payload, sentence, detail):
            seen["detail"] = detail
            raise Stop()

        plan = {"rows": [{"file_id": "5", "name": "Week 1.pptx", "folder_id": 1,
                          "size": 900, "pushed_at": "2026-09-01T00:00:00+00:00"}],
                "count": 1, "sentence": "Put 1 original back.", "fingerprint": {}}
        with mock.patch.object(gateway, "restore_plan", return_value=plan):
            with self.assertRaises(Stop):
                gateway.restore(self.app, self.cid, gateway.KINDS["pptx"], gate=gate)
        self.assertIsInstance(seen["detail"], str)
        self.assertEqual(json.loads(seen["detail"])[0]["label"], "Week 1.pptx")

    def test_batch_files_push_gate_gets_json_text(self):
        seen = {}

        def gate(payload, sentence, detail):
            seen["detail"] = detail
            raise Stop()

        plan = {"rows": [{"course": self.cid, "name": "Test Course", "error": "",
                          "kinds": {"pdf": {"ready": 2, "blocked": 0}}}],
                "ready": 2, "unverified": 0, "course_ids": [self.cid], "kinds": ["pdf"]}
        with mock.patch.object(batch_files, "push_plan", return_value=plan):
            with self.assertRaises(Stop):
                batch_files.push(self.app, [self.cid], ["pdf"], apply=True, gate=gate)
        self.assertIsInstance(seen["detail"], str)
        self.assertEqual(json.loads(seen["detail"])[0]["from"], "2 ready")


# --------------------------------------------------------- batch push ledger

class BatchFileUploadIsRecorded(ReviewBase):
    def test_each_course_and_kind_lands_in_the_ledger(self):
        """The per-course routes write the ledger line themselves. The batch
        path called the pipelines directly and wrote none, so a run that
        replaced files in twenty courses left every course's record empty."""
        plan = {"rows": [{"course": self.cid, "name": "Test Course", "error": "",
                          "kinds": {"pdf": {"ready": 1, "blocked": 0},
                                    "pptx": {"ready": 1, "blocked": 0}}}],
                "ready": 2, "unverified": 0, "course_ids": [self.cid], "kinds": ["pdf", "pptx"]}
        pdf_res = {"uploaded": [{"file_id": "11", "name": "handout.pdf"}], "failed": [],
                   "sentence_done": "Uploaded 1 fixed PDF over the originals in Test Course"}
        doc_res = {"uploaded": [{"file_id": "12", "name": "Week 1.pptx"}], "failed": [],
                   "sentence_done": "Uploaded 1 fixed PowerPoint file over the originals in Test Course"}
        with mock.patch.object(batch_files, "push_plan", return_value=plan), \
                mock.patch.object(batch_files.course_pdfs, "push", return_value=pdf_res), \
                mock.patch.object(batch_files.gateway, "push", return_value=doc_res):
            out = batch_files.push(self.app, [self.cid], ["pdf", "pptx"], apply=True,
                                   gate=lambda *a: None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["uploaded"], 2)
        rows = ledger.read(self.app.course_dir(self.cid))
        self.assertEqual(sorted(r["area"] for r in rows), ["docs", "pdf"])
        by_area = {r["area"]: r for r in rows}
        self.assertEqual(by_area["pdf"]["undo"]["route"], "/pdf/999/rollback")
        self.assertEqual(by_area["pdf"]["undo"]["body"]["file_ids"], ["11"])
        self.assertEqual(by_area["docs"]["undo"]["route"], "/a11y/999/pptx/restore")
        self.assertEqual(by_area["docs"]["count"], 1)

    def test_a_dry_run_records_nothing(self):
        plan = {"rows": [], "ready": 0, "unverified": 0, "course_ids": [self.cid], "kinds": ["pdf"]}
        with mock.patch.object(batch_files, "push_plan", return_value=plan):
            out = batch_files.push(self.app, [self.cid], ["pdf"], apply=False)
        self.assertTrue(out["dry_run"])
        self.assertEqual(ledger.read(self.app.course_dir(self.cid)), [])


# ------------------------------------------------------- survey row wording

class SurveyRowsReadTheRealStates(ReviewBase):
    def test_pdf_row_counts_what_course_pdfs_state_reports(self):
        """The row matched on "fixed" and "verified", words the PDF state never
        writes, so every repaired PDF surveyed as not ready and every unfetched
        one as scanned. The queue is grouped by reason, so its length is the
        number of reasons, not files."""
        state = {"files": [
            {"state": "not fetched"}, {"state": "backed up"}, {"state": "needs a person"},
            {"state": "fixed, not uploaded"}, {"state": "fixed, not uploaded"},
            {"state": "uploaded"}],
            "queue": [{"reason": "encrypted", "count": 2, "items": [{}, {}]},
                      {"reason": "no text", "count": 1, "items": [{}]}],
            "counts": {"alt_waiting": 4}, "listed_at": "2026-09-13T00:00:00+00:00",
            "needs": []}
        with mock.patch.object(batch_files.course_pdfs, "state", return_value=state):
            row = batch_files._pdf_row(self.app, self.cid)
        self.assertEqual(row["files"], 6)
        self.assertEqual(row["scanned"], 5)
        self.assertEqual(row["ready"], 2)
        self.assertEqual(row["uploaded"], 1)
        self.assertEqual(row["needs_person"], 3)
        self.assertEqual(row["alt_todo"], 4)

    def test_docs_row_carries_ready_uploaded_and_the_listing_time(self):
        kind = gateway.KINDS["pptx"]
        base = gateway.kind_dir(self.app, self.cid, kind)
        gateway.write_json(base / "files.json", {
            "at": "2026-09-13T01:00:00+00:00", "folders": {},
            "files": [{"id": 5, "display_name": "Week 1.pptx", "ext": ".pptx", "size": 10}]})
        d = base / "5"
        (d / "work").mkdir(parents=True)
        (d / "original.pptx").write_bytes(b"")
        gateway.write_json(d / "file.json", {"id": 5, "display_name": "Week 1.pptx",
                                             "ext": ".pptx", "size": 10, "fetched_at": "x"})
        gateway.write_json(d / "work" / "report.json", {"images": [], "tables": [], "untitled": [{"slide": 1}]})
        gateway.write_json(d / "work" / "fixes.json", {"titles": {"1": "Welcome"}, "table_headers": True})
        row = batch_files._docs_row(self.app, self.cid, "pptx")
        self.assertEqual(row["files"], 1)
        self.assertEqual(row["scanned"], 1)
        self.assertEqual(row["ready"], 1)
        self.assertEqual(row["uploaded"], 0)
        self.assertEqual(row["listed_at"], "2026-09-13T01:00:00+00:00")


# ---------------------------------------------------------- the Pages score

class PagesScoreReadsTheReportAsAList(ReviewBase):
    def test_a_verified_course_is_scored_not_unreadable(self):
        """verify-report.json is a list of records. Read as a dict it raised,
        and forecast() swallowed that into "could not be read" for Pages on
        exactly the courses that had been verified."""
        wd = workdir(self.app.course_dir(self.cid))
        manifest = {"items": [
            {"key": "page_a", "kind": "page", "id": "a", "title": "Week 1",
             "issues": ["no semantic heading (h2-h4)", "img missing alt"]},
            {"key": "page_b", "kind": "page", "id": "b", "title": "Week 2", "issues": []},
        ]}
        (wd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        report = [{"key": "page_a", "kind": "page", "id": "a", "ok": True, "issues": [],
                   "styled_file": "styled/page_a.html", "styled_sha256": "0" * 64}]
        (wd / "verify-report.json").write_text(json.dumps(report), encoding="utf-8")
        part = score.html(self.app, self.cid)
        self.assertEqual(part["state"], score.SCORED)
        self.assertEqual(part["checked"], 2)
        a = next(r for r in part["rows"] if r["id"] == "page_a")
        self.assertTrue(a["fixed"])
        self.assertGreater(a["after"], a["before"], "a restyle fixes the heading, not the alt")
        self.assertLess(a["after"], 100.0, "the missing alt text is still charged")
        whole = score.forecast(self.app, self.cid)
        pages = next(p for p in whole["parts"] if p["kind"] == "Pages")
        self.assertNotIn("error", pages)


# ----------------------------------------------------------- the web files

class FileComplianceScreen(unittest.TestCase):
    def test_uses_the_shell_modal_not_the_browser_confirm(self):
        """The front end contract rules confirm() out: it escapes the focus
        trap and cannot show the server's own sentence."""
        src = (WEB / "js" / "files.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?<![.\w])confirm\(", src))
        self.assertIn("openModal(", src)

    def test_describe_runs_are_drawn_as_descriptions(self):
        src = (WEB / "js" / "files.js").read_text(encoding="utf-8")
        self.assertIn("action === 'describe'", src)


class KindTabsAgree(unittest.TestCase):
    """a11y.js paints the kind tabs, pdf.js repaints the same strip for the
    direct #/c/<cid>/pdf route, and docs.js owns the document kinds. When the
    lists drifted, opening PDF triage grew a seventh tab and lost the group
    headers until the person left it. The three lists are read off the source,
    since there is no JavaScript runner here."""

    def _ids(self, name, const):
        src = (WEB / "js" / name).read_text(encoding="utf-8")
        block = re.search(r"const %s = \[(.*?)\n  \];" % const, src, re.S)
        self.assertIsNotNone(block, "%s has no %s list" % (name, const))
        return re.findall(r"\{\s*id: '([\w-]+)'", block.group(1))

    def test_a11y_and_pdf_paint_the_same_strip(self):
        self.assertEqual(self._ids("a11y.js", "KINDS"), self._ids("pdf.js", "KINDS"))

    def test_every_docs_kind_has_a_tab(self):
        tabs = set(self._ids("a11y.js", "KINDS"))
        for kind in self._ids("docs.js", "DOCS_KINDS"):
            self.assertIn(kind, tabs, "docs.js kind %r has no tab in a11y.js" % kind)
        self.assertIn("triage", tabs)


if __name__ == "__main__":
    unittest.main()
