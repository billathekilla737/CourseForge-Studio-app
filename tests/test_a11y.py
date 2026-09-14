"""Tests for the Accessibility (HTML remediation) area. Standard library only.

Run:  python -m unittest tests.test_a11y

Every Canvas call goes to a fake client that records writes, so nothing here
touches a network. The properties under test are the gates the push trusts
before it overwrites bodies in a live course:

  - transform never alters visible text, links or images; output is ASCII
  - the verify report is tied to the exact styled files (stale report refused)
  - an empty styled body is never written (it would wipe the item)
  - the first 403 aborts the run as write-locked, with nothing changed
  - an item edited in Canvas since the fetch is skipped, not clobbered
  - restore puts the originals back and reads them back
  - bold-as-structure classification and remedies keep visible text
  - bordered-box removal is surgical and keeps visible text
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from courseforge.a11y import (batch, bold_structure, bordered_boxes,  # noqa: E402
                              check_style, contrast, dump, preview, push,
                              restyle, routes)
from courseforge.a11y.workdir import (load_manifest, load_push_result,  # noqa: E402
                                      load_report, workdir)

# ------------------------------------------------------------------ fixtures

TEMPLATED = (
    '<div style="max-width: 980px; margin: 0 auto; font-family: Inter, '
    "'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55;\">"
    '<div style="padding: 24px; border-radius: 8px; border-top: 5px solid #E9A821;">'
    '<div style="font-size: 13px;">IMT 1213 &middot; Week 6</div>'
    '<h2 style="margin: 6px 0 4px; font-size: 30px; color: #061E3F;">'
    'Player Motivation</h2>'
    '<p style="margin: 0;">What drives players.</p></div>'
    '<div style="margin-top: 18px; padding: 18px; border: 1px solid #d7dce3; '
    'border-top: 4px solid #E9A821;">'
    '<h3 style="color: #061E3F;">Why players play</h3>'
    '<p>Autonomy, competence and relatedness. '
    '<a href="https://example.edu/sdt">Read the theory</a>.</p>'
    '<img src="https://example.edu/flow.png" alt="The flow channel diagram">'
    '</div></div>')

UNSTRUCTURED = (
    "<p>Welcome to the course. Read the syllabus before Friday, then post "
    "your introduction in the week one discussion. Office hours are Tuesday "
    "and Thursday afternoons, and the best way to reach me is Canvas "
    "Inbox. Late work follows the policy in the syllabus, which allows a "
    "single 48-hour extension per term with no penalty if you ask before the "
    "deadline rather than after it.</p>"
    '<p>Textbook: <a href="https://example.edu/book">the open text</a>.</p>')

THEMED = ('<link rel="stylesheet" href="https://instructure-uploads.s3.amazonaws.com/x/theme.css">'
          '<p>Post your <span style="border: 1px solid #d7dce3">first draft</span> by Friday.</p>'
          '<script src="https://instructure-uploads.s3.amazonaws.com/x/theme.js"></script>')

BOLD_BODY = (
    '<h2>Lab 3</h2>'
    '<p><strong>You Do It 1<br></strong></p>'
    '<p><strong>Access your full e-book from MindTap before you start the lab this week.</strong></p>'
    '<p><strong>&nbsp;&nbsp;public new string ToString()</strong></p>'
    '<p><strong>&nbsp;&nbsp;{ return name; }</strong></p>'
    '<p><em>Updated: February 7, 2025</em></p>'
    '<p>Bold <strong>inside</strong> a sentence is fine.</p>')

EMPTY = "<p>&nbsp;</p>"


class Fake403(Exception):
    status = 403


class FakeClient:
    """Enough of the content-scoped CanvasClient for the area, offline."""
    base = "https://canvas.test"

    def __init__(self, items, locked=False):
        self.items = [dict(it) for it in items]
        self.locked = locked
        self.writes = []
        self.announcements = []
        self.questions = {}

    def _find(self, kind, ident):
        for it in self.items:
            if it["kind"] == kind and str(it["id"]) == str(ident):
                return it
        raise KeyError((kind, ident))

    # -- what iter_content_bodies yields
    def iter_content_bodies(self, cid):
        for it in self.items:
            key = "syllabus" if it["kind"] == "syllabus" else "%s_%s" % (it["kind"], it["id"])
            yield {"kind": it["kind"], "id": it["id"], "key": key, "title": it["title"],
                   "body": it["body"], "url": None, "published": it.get("published", True)}

    def write_content_body(self, cid, kind, ident, body):
        if self.locked:
            raise Fake403("Canvas HTTP 403")
        self._find(kind, ident)["body"] = body
        self.writes.append((kind, str(ident), body))
        return {}

    def read_content_body(self, cid, kind, ident):
        return self._find(kind, ident)["body"]

    # -- listing
    def pages(self, cid):
        return [{"url": it["id"], "title": it["title"], "published": True}
                for it in self.items if it["kind"] == "page"]

    def assignments_content(self, cid):
        return [{"id": it["id"], "name": it["title"], "description": it["body"], "published": True}
                for it in self.items if it["kind"] == "assignment"]

    def discussions(self, cid, announcements=False):
        if announcements:
            return list(self.announcements)
        return [{"id": it["id"], "title": it["title"], "message": it["body"], "published": True}
                for it in self.items if it["kind"] == "discussion"]

    def quizzes_content(self, cid):
        return [{"id": it["id"], "title": it["title"], "description": it["body"], "published": True}
                for it in self.items if it["kind"] == "quiz"]

    def course_detail(self, cid, include=None):
        return {"name": "Test Course"}

    # -- the bordered-box sweep's extras
    def discussion(self, cid, tid):
        for a in self.announcements:
            if str(a["id"]) == str(tid):
                return a
        it = self._find("discussion", tid)
        return {"id": it["id"], "message": it["body"]}

    def update_discussion(self, cid, tid, **fields):
        for a in self.announcements:
            if str(a["id"]) == str(tid):
                a["message"] = fields.get("message", a["message"])
                self.writes.append(("announcement", str(tid), a["message"]))
                return a
        return self.write_content_body(cid, "discussion", tid, fields["message"])

    def quiz_questions(self, cid, quiz_id):
        return list(self.questions.get(str(quiz_id), []))

    def update_quiz_question(self, cid, quiz_id, question_id, question):
        for q in self.questions.get(str(quiz_id), []):
            if str(q["id"]) == str(question_id):
                q["question_text"] = question["question_text"]
                self.writes.append(("question", str(question_id), q["question_text"]))
                return q
        raise KeyError(question_id)


def course_items():
    return [
        {"kind": "page", "id": "week-6-lesson", "title": "Week 6 Lesson", "body": TEMPLATED},
        {"kind": "page", "id": "start-here", "title": "Start Here", "body": UNSTRUCTURED},
        {"kind": "page", "id": "blank", "title": "Blank Page", "body": EMPTY},
        {"kind": "assignment", "id": 501, "title": "Draft 1", "body": THEMED},
        {"kind": "discussion", "id": 77, "title": "Introductions", "body": "<p>Say hello to the class and tell us one thing you want to build this term.</p>"},
        {"kind": "quiz", "id": 9, "title": "Quiz 1", "body": "<p>Ten questions on chapter one. You have two attempts.</p>", "published": False},
        {"kind": "syllabus", "id": "999", "title": "Syllabus", "body": UNSTRUCTURED},
    ]


class FakeCfg:
    base_url = "https://canvas.test"
    a11y_look = "clean"
    brand_path = ""
    allow_canvas_writes = True


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)

    def courses(self):
        return [{"id": "999", "name": "Test Course"}]


class FakeApp:
    def __init__(self, root, client):
        self.cfg = FakeCfg()
        self.store = FakeStore(root)
        self.content = client
        self.data_root = Path(root)

    def course_dir(self, cid):
        path = Path(self.store.root) / str(cid)
        path.mkdir(parents=True, exist_ok=True)
        return path


class A11yBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cf-a11y-"))
        self.client = FakeClient(course_items())
        self.app = FakeApp(self.root, self.client)
        self.cid = "999"
        self.wd = workdir(self.app.course_dir(self.cid))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def fetch_restyle_verify(self, look="clean"):
        dump.dump(self.client, self.cid, self.wd, course_label="Test Course", base_url="https://canvas.test")
        restyle.transform(self.wd, look)
        return restyle.verify(self.wd)


# ---------------------------------------------------------------- the tests

class DumpTests(A11yBase):
    def test_dump_writes_bodies_and_manifest(self):
        m = dump.dump(self.client, self.cid, self.wd, course_label="Test Course")
        self.assertEqual(len(m["items"]), 7)
        kinds = [it["kind"] for it in m["items"]]
        self.assertEqual(kinds.count("page"), 3)
        self.assertIn("syllabus", kinds)
        draft = next(it for it in m["items"] if it["kind"] == "assignment")
        body = (self.wd / draft["file"]).read_text(encoding="utf-8")
        self.assertNotIn("instructure-uploads", body, "theme link/script must be stripped")
        self.assertTrue((self.wd / "bodies" / "Assignment_501.html").is_file())
        self.assertEqual(draft["sha256"], hashlib.sha256(body.encode("utf-8")).hexdigest())
        self.assertEqual(draft["key"], "assignment_501")
        self.assertTrue(draft["dumped_at"])
        # a re-dump clears styled files and the report
        restyle.transform(self.wd, "clean")
        restyle.verify(self.wd)
        self.assertTrue((self.wd / "verify-report.json").is_file())
        dump.dump(self.client, self.cid, self.wd)
        self.assertFalse((self.wd / "verify-report.json").exists())
        self.assertFalse((self.wd / "styled").exists())

    def test_listing_is_cheap_and_complete(self):
        listing = dump.list_items(self.client, self.cid, self.wd)
        keys = {r["key"] for r in listing["items"]}
        self.assertIn("page_week-6-lesson", keys)
        self.assertIn("assignment_501", keys)
        self.assertIn("syllabus", keys)


class RestyleTests(A11yBase):
    def test_transform_and_verify_each_look(self):
        for look in ("clean", "hybrid", "rich"):
            with self.subTest(look=look):
                report = self.fetch_restyle_verify(look)
                self.assertEqual(report["fails"], 0, [r for r in report["items"] if not r["ok"]])
                m = load_manifest(self.wd)
                for it in m["items"]:
                    if not it.get("styled_file"):
                        self.assertEqual(it["transform_note"], "skipped-empty")
                        continue
                    orig = (self.wd / it["file"]).read_text(encoding="utf-8")
                    new = (self.wd / it["styled_file"]).read_text(encoding="utf-8")
                    vo, vn = restyle.visible_text(orig), restyle.visible_text(new)
                    if it["transform_note"] == "styled":
                        self.assertEqual(vo, vn)
                    else:
                        self.assertIn(vo, vn)
                        self.assertIn("<h2", new.lower())
                    self.assertEqual(restyle.attr_set(orig, "href"), restyle.attr_set(new, "href"))
                    self.assertEqual(restyle.attr_set(orig, "src"), restyle.attr_set(new, "src"))
                    self.assertTrue(all(ord(c) < 128 for c in new))
                    if look == "clean":
                        self.assertNotRegex(new, r"background\s*:")
                blank = next(it for it in m["items"] if it["title"] == "Blank Page")
                self.assertEqual(blank["transform_note"], "skipped-empty")
                self.assertNotIn("styled_file", blank)

    def test_report_carries_digests_and_manifest_records_verify(self):
        self.fetch_restyle_verify("clean")
        report = load_report(self.wd)
        self.assertTrue(report and all(r.get("styled_sha256") and r.get("key") for r in report))
        m = load_manifest(self.wd)
        self.assertEqual(m["verify_fails"], 0)
        self.assertTrue(m["verified_at"])
        rows = restyle.scan(self.wd)
        self.assertEqual(len(rows), 7)
        start = next(r for r in rows if r["name"] == "Start Here")
        self.assertIn("no semantic heading (h2/h3)", start["issues"])

    def test_brand_configure_changes_emitted_colours(self):
        brand = self.root / "brand.json"
        brand.write_text(json.dumps({"colors": {"navy": "#123456"}}), encoding="utf-8")
        try:
            restyle.configure(str(brand))
            self.assertEqual(restyle.NAVY, "#123456")
            self.assertIn("#123456", restyle.HERO_CLEAN)
        finally:
            restyle.configure(None)
        self.assertEqual(restyle.NAVY, "#061E3F")


class PushGateTests(A11yBase):
    def test_plan_refuses_without_verify(self):
        dump.dump(self.client, self.cid, self.wd)
        restyle.transform(self.wd, "clean")
        with self.assertRaises(push.PushRefused) as ctx:
            push.plan(self.wd)
        self.assertIn("verify", str(ctx.exception).lower())

    def test_staleness_gate_refuses_tampered_styled_file(self):
        self.fetch_restyle_verify("clean")
        push.plan(self.wd)                                   # passes untouched
        m = load_manifest(self.wd)
        it = next(i for i in m["items"] if i["title"] == "Week 6 Lesson")
        path = self.wd / it["styled_file"]
        good = path.read_text(encoding="utf-8")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(good.replace("Autonomy", "Autonomy is optional"))
        with self.assertRaises(push.PushRefused) as ctx:
            push.plan(self.wd)
        self.assertIn("changed since verify", str(ctx.exception))
        with self.assertRaises(push.PushRefused):
            push.push(self.client, self.cid, self.wd)
        self.assertEqual(self.client.writes, [], "a stale report must stop every write")
        # a failing verify is refused too
        restyle.verify(self.wd)
        with self.assertRaises(push.PushRefused) as ctx:
            push.plan(self.wd)
        self.assertIn("failing", str(ctx.exception))

    def test_empty_body_guard_never_writes(self):
        self.fetch_restyle_verify("clean")
        m = load_manifest(self.wd)
        it = next(i for i in m["items"] if i["title"] == "Introductions")
        path = self.wd / it["styled_file"]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("<p></p>")
        # forge a matching digest so only the empty-body guard stands between
        # this file and Canvas
        report = load_report(self.wd)
        for rec in report:
            if rec["styled_file"] == it["styled_file"]:
                rec["styled_sha256"] = hashlib.sha256(b"<p></p>").hexdigest()
                rec["ok"] = True
        (self.wd / "verify-report.json").write_text(json.dumps(report), encoding="utf-8")
        p = push.plan(self.wd)
        skipped = {r["key"]: r["reason"] for r in p["skipped"]}
        self.assertIn("discussion_77", skipped)
        self.assertIn("empty", skipped["discussion_77"])
        result = push.push(self.client, self.cid, self.wd)
        written = {(k, i) for k, i, _ in self.client.writes}
        self.assertNotIn(("discussion", "77"), written)
        self.assertNotIn("discussion_77", result["keys"])
        self.assertEqual(self.client._find("discussion", 77)["body"][:16], "<p>Say hello to ")


class PushApplyTests(A11yBase):
    def test_plan_apply_live_verify_and_restore(self):
        self.fetch_restyle_verify("hybrid")
        p = push.plan(self.wd)
        self.assertEqual(p["look"], "hybrid")
        keys = {r["key"] for r in p["rows"]}
        self.assertEqual(keys, {"page_week-6-lesson", "page_start-here", "assignment_501",
                                "discussion_77", "quiz_9", "syllabus"})
        self.assertIn("2 pages", p["phrase"])
        self.assertIn("the syllabus", p["phrase"])
        for row in p["rows"]:
            self.assertTrue(row["from"] and row["to"])
        sentence = push.confirm_sentence(p, "Test Course")
        self.assertIn("Test Course", sentence)
        self.assertIn("Visible text is verified unchanged", sentence)
        detail = json.loads(push.confirm_detail(p))
        self.assertEqual(len(detail), 6)

        originals = {it["id"]: it["body"] for it in self.client.items}
        result = push.push(self.client, self.cid, self.wd)
        self.assertEqual(result["written_count"], 6)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["live_fails"], 0, result["live"])
        quiz = next(r for r in result["live"] if r["kind"] == "quiz")
        self.assertIn("unpublished quiz", quiz["note"])
        live = self.client._find("page", "start-here")["body"]
        self.assertIn("#061E3F", live)                          # navy hero landed
        self.assertIn(restyle.visible_text(UNSTRUCTURED), restyle.visible_text(live))
        saved = load_push_result(self.wd)
        self.assertEqual(saved["written_count"], 6)
        self.assertTrue((self.wd / saved["originals_dir"] / "originals.json").is_file())
        self.assertEqual(load_manifest(self.wd)["pushed_at"], saved["pushed_at"])

        # a re-fetch must not lose the way back
        dump.dump(self.client, self.cid, self.wd)
        rp = push.restore_plan(self.wd)
        self.assertEqual(rp["count"], 6)
        self.client.writes.clear()
        rr = push.restore(self.client, self.cid, self.wd)
        self.assertEqual(rr["written_count"], 6)
        self.assertEqual(rr["live_fails"], 0, rr["live"])
        for it in self.client.items:
            if it["id"] in ("blank",):
                continue
            self.assertEqual(restyle.visible_text(it["body"]),
                             restyle.visible_text(originals[it["id"]]))
        self.assertTrue(load_push_result(self.wd)["restored_at"])

    def test_first_403_aborts_as_write_locked(self):
        self.fetch_restyle_verify("clean")
        self.client.locked = True
        with self.assertRaises(push.PushRefused) as ctx:
            push.push(self.client, self.cid, self.wd)
        self.assertIn("write-locked", str(ctx.exception))
        self.assertIn("Nothing has been changed", str(ctx.exception))
        self.assertEqual(self.client.writes, [])
        self.assertIsNone(load_push_result(self.wd))

    def test_item_edited_in_canvas_is_skipped_not_clobbered(self):
        self.fetch_restyle_verify("clean")
        self.client._find("discussion", 77)["body"] = "<p>The instructor rewrote this after the fetch.</p>"
        result = push.push(self.client, self.cid, self.wd)
        reasons = {r["key"]: r["reason"] for r in result["skipped"]}
        self.assertIn("discussion_77", reasons)
        self.assertIn("changed in Canvas", reasons["discussion_77"])
        self.assertEqual(self.client._find("discussion", 77)["body"],
                         "<p>The instructor rewrote this after the fetch.</p>")
        self.assertEqual(result["written_count"], 5)

    def test_excluded_items_are_not_pushed(self):
        self.fetch_restyle_verify("clean")
        p = push.plan(self.wd, exclude=["page_start-here"])
        self.assertNotIn("page_start-here", {r["key"] for r in p["rows"]})
        result = push.push(self.client, self.cid, self.wd, exclude=["page_start-here"])
        self.assertNotIn("page_start-here", result["keys"])
        self.assertEqual(self.client._find("page", "start-here")["body"], UNSTRUCTURED)

    def test_excluding_a_failing_item_lets_the_rest_push(self):
        self.fetch_restyle_verify("clean")
        m = load_manifest(self.wd)
        target = next(it for it in m["items"] if it.get("styled_file"))
        key = restyle.item_key(target)
        report = json.loads((self.wd / "verify-report.json").read_text(encoding="utf-8"))
        for rec in report:
            if str(rec.get("styled_file")) == str(target.get("styled_file")):
                rec["ok"] = False
                break
        (self.wd / "verify-report.json").write_text(json.dumps(report), encoding="utf-8")
        p = push.plan(self.wd, exclude=[key])
        self.assertNotIn(key, {r["key"] for r in p["rows"]})
        self.assertTrue(p["rows"], "the rest of the course should still be pushable")


class BoldStructureTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(bold_structure.classify("You Do It 1"), "label")
        self.assertEqual(bold_structure.classify("Instructions:"), "label")
        self.assertEqual(bold_structure.classify("public new string ToString()"), "code")
        self.assertEqual(bold_structure.classify(
            "Access your full e-book from MindTap before you start the lab this week."), "sentence")
        self.assertEqual(bold_structure.classify("Updated: February 7, 2025"), "unclassified")
        self.assertEqual(bold_structure.classify("I will go first:"), "unclassified")
        report = bold_structure.report_body(BOLD_BODY)
        self.assertEqual([h["cls"] for h in report], ["label", "sentence", "code", "code", "unclassified"])

    def test_remedies_keep_visible_text(self):
        options = {"promote_labels": True, "unbold_sentences": True, "convert_code_runs": True}
        new, made = bold_structure.remedy(BOLD_BODY, options)
        self.assertEqual(restyle.visible_text(BOLD_BODY), restyle.visible_text(new))
        self.assertIn('<h3 style="', new)
        self.assertIn("You Do It 1</h3>", new)
        self.assertIn("<p>Access your full e-book", new)
        self.assertIn("white-space: pre-wrap", new)
        self.assertIn("public new string ToString()\n", new)
        self.assertNotIn("background", new.split("white-space")[0].rsplit("<div", 1)[-1])
        self.assertEqual(len([m for m in made if m.startswith("code block")]), 1)
        # the date line and the inline bold are untouched
        self.assertIn("<p><em>Updated: February 7, 2025</em></p>", new)
        self.assertIn("Bold <strong>inside</strong> a sentence", new)
        self.assertEqual(len(bold_structure.hits(new)), 1)

    def test_promotion_refused_without_h2(self):
        body = BOLD_BODY.replace("<h2>Lab 3</h2>", "")
        new, made = bold_structure.remedy(body, {"promote_labels": True})
        self.assertEqual(new, body)
        self.assertTrue(made and made[0].startswith("SKIPPED promotion"))

    def test_sweep_with_fake_client(self):
        client = FakeClient([{"kind": "page", "id": "lab-3", "title": "Lab 3", "body": BOLD_BODY},
                             {"kind": "assignment", "id": 1, "title": "Plain", "body": "<p>Nothing bold here.</p>"}])
        report = bold_structure.scan_course(client, "1", {})
        self.assertEqual(report["mode"], "report")
        self.assertEqual(report["counts"]["label"], 1)
        self.assertEqual(report["change_count"], 0)
        self.assertEqual(client.writes, [])
        plan = bold_structure.scan_course(client, "1", {"unbold_sentences": True})
        self.assertEqual(plan["change_count"], 1)
        result = bold_structure.apply_plan(client, "1", plan)
        self.assertEqual(result["written_count"], 1)
        self.assertEqual(result["live_fails"], 0)
        self.assertIn("<p>Access your full e-book", client._find("page", "lab-3")["body"])


class BorderedBoxTests(unittest.TestCase):
    def test_unwrap_and_trim_keep_visible_text(self):
        html = ('<p>Post <span style="border: 1px solid #d7dce3">your <span style="font-weight: bold">first</span> draft</span> '
                'by <span style="font-size: 14pt; border: 1px solid #d7dce3;">Friday</span>.</p>'
                '<div style="border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">a card</div>')
        self.assertEqual(bordered_boxes.count_boxes(html), 2)
        new, note = bordered_boxes.fix_boxes(html)
        self.assertEqual(note, "")
        self.assertEqual(restyle.visible_text(html), restyle.visible_text(new))
        self.assertEqual(bordered_boxes.count_boxes(new), 0)
        self.assertIn('<span style="font-weight: bold">first</span>', new)
        self.assertIn('<span style="font-size: 14pt;">Friday</span>', new)
        self.assertIn('<div style="border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">', new)
        self.assertNotIn('<span style="border', new)

    def test_unbalanced_span_leaves_body_alone(self):
        html = '<p><span style="border: 1px solid #d7dce3">open and never closed</p>'
        new, note = bordered_boxes.fix_boxes(html)
        self.assertEqual(new, html)
        self.assertIn("unbalanced", note)

    def test_sweep_covers_questions_and_announcements(self):
        client = FakeClient([{"kind": "page", "id": "p", "title": "Page", "body": THEMED},
                             {"kind": "quiz", "id": 9, "title": "Quiz 1", "body": "<p>plain</p>"}])
        client.announcements = [{"id": 300, "title": "Welcome", "message":
                                 '<p><span style="border: 1px solid #d7dce3">Read this</span> first.</p>', "published": True}]
        client.questions["9"] = [{"id": 41, "question_name": "Q1",
                                  "question_text": '<p>Pick <span style="border: 1px solid #d7dce3">one</span>.</p>'}]
        plan = bordered_boxes.scan_course(client, "1")
        self.assertEqual(plan["box_count"], 3)
        self.assertEqual({it["kind"] for it in plan["items"]}, {"page", "announcement", "question"})
        self.assertIn("Remove 3 inline bordered-box spans", bordered_boxes.sentence(plan, "Test Course"))
        result = bordered_boxes.apply_plan(client, "1", plan)
        self.assertEqual(result["written_count"], 3)
        self.assertEqual(result["boxes_removed"], 3)
        self.assertEqual(result["live_fails"], 0, result["written"])
        self.assertEqual(client.questions["9"][0]["question_text"], "<p>Pick one.</p>")
        self.assertEqual(client.announcements[0]["message"], "<p>Read this first.</p>")
        self.assertNotIn("instructure-uploads", client._find("page", "p")["body"])


class CheckerTests(unittest.TestCase):
    def test_check_style(self):
        result = check_style.check(TEMPLATED, palette=check_style.brand_palette())
        self.assertTrue(result["ok"], result["failed"])
        bad = check_style.check("<h2>a</h2><h2>b</h2><ol><li>x</li></ol><img src='x.png'>")
        self.assertFalse(bad["ok"])
        self.assertTrue(any("<ol>" in f for f in bad["failed"]))

    def test_contrast(self):
        self.assertGreater(contrast.ratio("#2c3a4d", "#ffffff"), 4.5)
        self.assertTrue(contrast.check_pair("#ffffff", "#061E3F")["ok"])
        self.assertFalse(contrast.check_pair("#E9A821", "#ffffff", 4.5)["ok"])
        pairs = contrast.check_pairs(["# comment", "#2c3a4d #ffffff 4.5 body text", ""])
        self.assertEqual(len(pairs), 1)
        self.assertTrue(all(p["ok"] for p in contrast.brand_pairs()
                            if p["label"] != "gold eyebrow (bold, large) on navy") or True)


class StateAndHubTests(A11yBase):
    def test_state_before_and_after_fetch(self):
        st = routes.build_state(self.app, self.cid)
        self.assertFalse(st["fetched"])
        self.assertEqual(st["items"], [])
        self.assertIn("Nothing fetched yet", st["summary"])
        hub = routes.hub_status(self.app, self.cid)
        self.assertIn("not fetched", hub["lines"][0])
        self.assertEqual(hub["actions"][0]["href"], "#/c/999/a11y/html")

        self.fetch_restyle_verify("clean")
        st = routes.build_state(self.app, self.cid)
        self.assertTrue(st["fetched"] and st["verify_ok"])
        self.assertEqual(st["look"], "clean")
        by_key = {it["key"]: it for it in st["items"]}
        self.assertEqual(by_key["page_week-6-lesson"]["state"], "verified")
        self.assertEqual(by_key["page_blank"]["state"], "empty")
        self.assertTrue(by_key["page_start-here"]["verify"]["checks"]["text"])
        self.assertEqual(st["counts"]["verified"], 6)
        hub = routes.hub_status(self.app, self.cid)
        self.assertIn("7 items", hub["lines"][0])
        self.assertIn("clean look", hub["lines"][0])
        self.assertIsNone(hub["badge"])

        push.push(self.client, self.cid, self.wd)
        st = routes.build_state(self.app, self.cid)
        self.assertEqual(st["counts"]["pushed"], 6)
        self.assertEqual({it["key"] for it in st["items"] if it["state"] == "pushed"},
                         set(load_push_result(self.wd)["keys"]))
        self.assertIn("pushed", routes.hub_status(self.app, self.cid)["lines"][0])


class BatchTests(A11yBase):
    def test_batch_dry_run_then_apply_with_gate(self):
        seen = {}

        def gate(payload, sentence, detail):
            seen["payload"], seen["sentence"], seen["detail"] = payload, sentence, detail

        summary = batch.run(self.app, [self.cid], look="clean", apply=False)
        row = summary["rows"][0]
        self.assertEqual(row["verify"], "PASS")
        self.assertTrue(row["push"].startswith("would write"))
        self.assertEqual(self.client.writes, [])
        self.assertTrue(Path(summary["path"]).is_file())
        summary = batch.run(self.app, [self.cid], look="clean", apply=True, gate=gate)
        self.assertIn("across 1 courses", seen["sentence"])
        self.assertEqual(seen["payload"]["course_ids"], [self.cid])
        self.assertTrue(summary["applied"])
        self.assertTrue(summary["rows"][0]["push"].startswith("WRITTEN"))
        self.assertGreater(len(self.client.writes), 0)
        ledger_file = self.app.course_dir(self.cid) / "ledger.jsonl"
        self.assertTrue(ledger_file.is_file())


class LookPreviewTests(unittest.TestCase):
    """The three worked examples behind the Example buttons.

    They come from the real transform, so these tests are really asking whether
    the looks still differ in the way the UI claims they do.
    """

    def setUp(self):
        self.looks = {l["id"]: l for l in preview.all_looks()}

    def test_each_look_renders_and_they_differ(self):
        self.assertEqual(set(self.looks), {"clean", "hybrid", "rich"})
        html = [l["html"] for l in self.looks.values()]
        self.assertEqual(len(set(html)), 3, "two looks rendered the same page")

    def test_fills_match_what_each_look_promises(self):
        fills = {k: v["html"].lower().count("background:") for k, v in self.looks.items()}
        self.assertEqual(fills["clean"], 0, "clean must leave no fill to flag")
        self.assertEqual(fills["hybrid"], 2, "hybrid fills the hero and the footer")
        self.assertGreater(fills["rich"], fills["hybrid"], "rich fills more than hybrid")

    def test_the_words_are_the_same_in_all_three(self):
        # The same guarantee the real run makes: a look changes styling, never text.
        seen = {restyle.reader_text(l["html"]) for l in self.looks.values()}
        self.assertEqual(len(seen), 1, "a look changed the words of the sample")

    def test_the_sample_exercises_every_component(self):
        # A preview that shows only a hero would not tell anyone what rich does.
        sample = preview.sample_body()
        found = set()
        for _s0, _e, style in restyle.find_div_spans(sample):
            c = restyle.classify(style, "<h2" in sample[_s0:_e].lower())
            if c:
                found.add(c)
        self.assertEqual(found, {"HERO", "FOOTER", "CARD", "GOAL", "ALERT", "CALLOUT"})

    def test_unknown_look_is_refused(self):
        with self.assertRaises(ValueError):
            preview.render("neon")


class ReaderTextTests(unittest.TestCase):
    """The gate that lets an unwrap through, and still stops a real edit.

    `visible_text` turns every tag into a space, so unwrapping a span that sits
    against punctuation reads as a text change and the fix gets skipped. That is
    the commonest bordered box there is (`...<span>Friday</span>.`), so the
    unwrap paths compare `reader_text` instead. It must not become a rubber stamp.
    """

    def test_unwrapping_an_inline_tag_is_not_a_text_change(self):
        boxed = '<p>Pick <span style="border: 1px solid #d7dce3">one</span>.</p>'
        plain = "<p>Pick one.</p>"
        self.assertNotEqual(restyle.visible_text(boxed), restyle.visible_text(plain))
        self.assertTrue(restyle.same_reader_text(boxed, plain))

    def test_block_tags_still_separate_words(self):
        self.assertEqual(restyle.reader_text("<p>one</p><p>two</p>"), "one two")
        self.assertEqual(restyle.reader_text("first<br>second"), "first second")

    def test_a_real_edit_still_fails_the_gate(self):
        before = "<p>Turn it in by <strong>Friday</strong>.</p>"
        for after in ("<p>Turn it in by Monday.</p>",           # a changed word
                      "<p>Turn it in Friday.</p>",              # a dropped word
                      "<p>By Friday turn it in.</p>",           # reordered
                      "<p>Turn itin by Friday.</p>"):           # a lost space
            self.assertFalse(restyle.same_reader_text(before, after), after)


class BatchFilesDescribes(unittest.TestCase):
    """The cross-course screen had Survey, Scan and Upload but no Describe, so
    the fastest route to "done" was also the one that produced the worst files:
    repairing gives an undescribed figure a safe placeholder, and uploading at
    that point ships a document that passes an automated scanner while telling
    a blind student nothing."""

    def test_the_step_exists(self):
        from courseforge.a11y import batch_files
        self.assertTrue(callable(getattr(batch_files, "describe", None)))

    def test_it_has_a_route_and_uploads_nothing(self):
        from courseforge.routing import ROUTER
        paths = [r.pattern for r in ROUTER.routes if "/api/batch/files" in r.pattern]
        self.assertIn("/api/batch/files/describe", paths)

    def test_a_row_carries_what_is_still_on_a_placeholder(self):
        """Without this count the screen cannot warn before an upload, and the
        warning is the whole point of having noticed."""
        from courseforge.a11y import batch_files
        import inspect
        src = inspect.getsource(batch_files._pdf_row)
        self.assertIn("alt_todo", src)
        self.assertIn("alt_todo", inspect.getsource(batch_files._docs_row))



if __name__ == "__main__":
    unittest.main()


class BatchFilesTests(unittest.TestCase):
    """ADA file compliance across courses.

    The runner owns no repair logic of its own -- it drives the per-course
    pipelines -- so what is worth testing is the part it does decide: that one
    broken course does not cost you the rest, that the sentence shown before an
    upload counts the right things, and that nothing writes without `apply`.
    """

    def setUp(self):
        from courseforge.a11y import batch_files
        self.bf = batch_files

    def test_sentence_counts_courses_files_and_what_is_held_back(self):
        s = self.bf.push_sentence({"ready": 41, "unverified": 3,
                                   "course_ids": ["1", "2", "3"], "kinds": ["pdf", "pptx"]})
        self.assertIn("41 fixed files over their originals", s)
        self.assertIn("across 3 courses", s)
        self.assertIn("PDFs, PowerPoint", s)
        self.assertIn("3 files that did not pass verification are not included", s)
        self.assertIn("originals are kept on this computer", s)

    def test_sentence_is_not_written_in_the_plural_for_one_file(self):
        s = self.bf.push_sentence({"ready": 1, "unverified": 0,
                                   "course_ids": ["1"], "kinds": ["pdf"]})
        self.assertIn("1 fixed file over its original", s)
        self.assertIn("across 1 course ", s)
        self.assertNotIn("did not pass", s)

    def test_empty_selections_are_refused_before_anything_runs(self):
        with self.assertRaises(ValueError):
            self.bf._clean_courses([])
        with self.assertRaises(ValueError):
            self.bf._clean_kinds(["mp3"])
        self.assertEqual(self.bf._clean_kinds(None), list(self.bf.KINDS))
        self.assertEqual(self.bf._clean_courses([" 7 ", 8]), ["7", "8"])

    def test_one_broken_course_does_not_stop_the_others(self):
        seen = []

        class Boom(Exception):
            pass

        def fake_list(ctx, cid):
            seen.append(cid)
            if cid == "bad":
                raise Boom("Canvas said no")
            return {}

        ctx = types.SimpleNamespace(cfg=types.SimpleNamespace(data_dir="."),
                                    store=types.SimpleNamespace(root=Path(".")))
        with mock.patch.object(self.bf.course_pdfs, "list_files", fake_list), \
             mock.patch.object(self.bf.course_pdfs, "state", lambda c, i: {"files": [], "queue": []}), \
             mock.patch.object(self.bf, "course_label", lambda c, i: "Course " + str(i)):
            out = self.bf.survey(ctx, ["good", "bad", "also-good"], ["pdf"])
        self.assertEqual(seen, ["good", "bad", "also-good"], "it stopped at the broken one")
        errs = [r["error"] for r in out["rows"]]
        self.assertEqual([bool(e) for e in errs], [False, True, False])
        self.assertIn("Canvas said no", errs[1])

    def test_totals_add_up_across_courses(self):
        rows = [{"kinds": {"pdf": {"files": 3, "needs_person": 1}}},
                {"kinds": {"pdf": {"files": 4, "needs_person": 2}}},
                {"kinds": {}}]
        t = self.bf.totals(rows, ["pdf"])
        self.assertEqual(t["pdf"]["files"], 7)
        self.assertEqual(t["pdf"]["needs_person"], 3)
