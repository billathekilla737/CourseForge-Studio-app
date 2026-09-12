"""Tests for the Build content area. unittest, no network, no model call.

    python -m unittest tests.test_content -v

Covers: manifest validation, verify_slots on fixtures, check_style and
check_quiz on good and bad inputs, the question READ -> WRITE mapping, the
module-wipe gate with a fake client, the push_pages dry-run plan and apply
with a fake client, the project plan, rubrics plan, the drafting prompt
(palette, humanize rules, strict JSON) and the placement sentence.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from courseforge.canvas_content import ContentOps  # noqa: E402
from courseforge.content import (check_quiz, check_style, diff_content, generate,  # noqa: E402
                                 manifest as mf, place, push_pages, push_project,
                                 rubrics, state as statemod, verify_slots)
from courseforge.content.paths import BuildDir  # noqa: E402
from courseforge.style import HUMANIZE_RULES  # noqa: E402

GOOD_HTML = (
    '<div style="max-width: 980px; margin: 0 auto; font-family: Inter, Arial, sans-serif; '
    'line-height: 1.55; color: #2c3a4d;">'
    '<div style="padding: 24px; border-radius: 8px; background: #061E3F; border-top: 5px solid #E9A821;">'
    '<div style="font-size: 13px; color: #E9A821; font-weight: 700;">IMT 1213 &middot; Week 3 &middot; Lesson</div>'
    '<h2 style="margin: 6px 0 4px; font-size: 30px; color: #ffffff;">Week 3: Loops</h2>'
    '<p style="margin: 0; font-size: 15px; color: #cfdcec;">Repeating work without repeating code.</p></div>'
    '<div style="margin-top: 18px; padding: 18px; border-radius: 8px; background: #ffffff; '
    'border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">'
    '<h3 style="margin: 0 0 12px; font-size: 19px; color: #061E3F;">Why loops</h3>'
    '<p style="margin: 0; font-size: 14px;">A loop runs a block until a condition fails. '
    '<a href="https://example.edu/loops" style="color: #1565C0; text-decoration: underline;">Read the reference</a>.</p>'
    '<img src="https://example.edu/loop.png" alt="Flowchart of a while loop with one exit">'
    '</div></div>')

BAD_HTML = (
    '<div class="wrap" id="main"><h2>One</h2><h2>Two</h2><h4>Deep</h4>'
    '<ol><li>a</li></ol><br><em>x</em><style>p{}</style>'
    '<ul><li>outer<ul><li>inner</li></ul></li></ul>'
    '<table><tr><td>no th</td></tr></table>'
    '<img src="x.png" alt="x.png"><img src="y.png">'
    '<a href="https://e.edu"></a>'
    '<p style="color: #cfdcec; background: #ffffff;">low contrast</p></div>')

PROJECT_MANIFEST = {
    "course_label": "IMT 2772 Simulation and Game Project",
    "syllabus_file": "syllabus.html",
    "pages": [
        {"slug": "home", "title": "Home", "file": "home.html", "front_page": True},
        {"slug": "week-1", "title": "Week 1: Kickoff", "file": "week1.html"},
    ],
    "assignments": [
        {"key": "gdd", "name": "Game Design Document", "file": "assign-gdd.html",
         "points": 25, "submission_types": ["online_upload"], "group": "Assignments"},
    ],
    "discussions": [
        {"key": "standup-1", "title": "Week 1 Standup", "file": "standup.html", "points": 10,
         "note": "Team locked in.", "note_label": "This week:"},
    ],
    "quizzes": [
        {"key": "final", "title": "Final Exam", "file": "final.html", "group": "Final Exam",
         "time_limit": 60, "shuffle_answers": True,
         "questions": [
             {"text": "MDA stands for Mechanics, Dynamics and ___.", "type": "short_answer_question",
              "points": 3, "answers": [{"text": "Aesthetics", "correct": True}]},
             {"text": "A core loop is the smallest repeatable cycle.", "type": "true_false_question",
              "points": 2, "answers": [{"text": "True", "correct": True}, {"text": "False", "correct": False}]},
             {"text": "Which is a reward schedule?", "type": "multiple_choice_question", "points": 5,
              "answers": [{"text": "Variable ratio", "correct": True}, {"text": "Greybox", "correct": False},
                          {"text": "Magic circle", "correct": False}, {"text": "Juice", "correct": False}]},
         ]},
    ],
    "modules": [
        {"name": "Start Here", "items": [{"type": "Page", "slug": "home"},
                                        {"type": "SubHeader", "title": "Read the syllabus first."}]},
        {"name": "Week 1", "items": [{"type": "Page", "slug": "week-1"},
                                     {"type": "Assignment", "key": "gdd"},
                                     {"type": "Discussion", "key": "standup-1"},
                                     {"type": "Quiz", "key": "final"}]},
    ],
}

PAGES_MANIFEST = {
    "course_label": "IMT 1213",
    "pages": [
        {"key": "n1", "title": "Week 3: Loops", "file": "week3.html", "module": "Week 3",
         "module_position": 3, "position": 1},
        {"key": "n2", "title": "Week 4: Functions", "file": "week4.html", "module": "Week 4",
         "module_position": 4, "position": 1},
        {"key": "n3", "title": "Week 5: Missing", "file": "nope.html", "module": "Week 5"},
    ],
}


def hero_page(title: str) -> str:
    return GOOD_HTML.replace("Week 3: Loops", title)


class FakeClient:
    """Enough of ContentOps to plan and apply against, recording every write."""

    def __init__(self, modules=None, pages=None, assignments=None, discussions=None, quizzes=None):
        self._modules = list(modules or [])
        self._pages = {p["url"]: p for p in (pages or [])}
        self._assignments = list(assignments or [])
        self._discussions = list(discussions or [])
        self._quizzes = list(quizzes or [])
        self._questions: dict[int, list] = {}
        self._groups = [{"id": 1, "name": "Assignments"}]
        self._rubrics: list[dict] = []
        self.writes: list[tuple] = []
        self._next = 1000
        self.syllabus = "<p>old syllabus</p>"

    def _id(self):
        self._next += 1
        return self._next

    # reads
    def modules(self, cid, include_items=False):
        return list(self._modules)

    def pages(self, cid):
        return [{"url": u, "title": p.get("title")} for u, p in self._pages.items()]

    def page(self, cid, slug):
        return dict(self._pages[slug])

    def assignments_content(self, cid):
        return list(self._assignments)

    def discussions(self, cid, announcements=False):
        return list(self._discussions)

    def quizzes_content(self, cid):
        return list(self._quizzes)

    def quiz_questions(self, cid, qid):
        return list(self._questions.get(qid, []))

    def assignment_groups(self, cid):
        return list(self._groups)

    def course_detail(self, cid, include=None):
        return {"id": cid, "name": "Fake Course", "syllabus_body": self.syllabus}

    def rubrics(self, cid):
        return list(self._rubrics)

    def rubric(self, cid, rid, include=None):
        return next(r for r in self._rubrics if r["id"] == rid)

    # writes
    def create_page(self, cid, title, body, published=False, front_page=False, editing_roles=None):
        slug = mf.slugify(title)
        self._pages[slug] = {"url": slug, "page_id": self._id(), "title": title, "body": body,
                             "published": published}
        self.writes.append(("create_page", slug, published))
        return dict(self._pages[slug])

    def update_page(self, cid, slug, body=None, title=None, published=None, front_page=None):
        p = self._pages.setdefault(slug, {"url": slug, "page_id": self._id()})
        if body is not None:
            p["body"] = body
        if title is not None:
            p["title"] = title
        if published is not None:
            p["published"] = published
        self.writes.append(("update_page", slug, published))
        return dict(p)

    def update_course_content(self, cid, **fields):
        if "syllabus_body" in fields:
            self.syllabus = fields["syllabus_body"]
        self.writes.append(("update_course", tuple(sorted(fields))))
        return {"id": cid}

    def create_assignment_group(self, cid, name, weight=None):
        g = {"id": self._id(), "name": name}
        self._groups.append(g)
        self.writes.append(("create_group", name))
        return g

    def ensure_assignment_group(self, cid, name):
        for g in self._groups:
            if g["name"].lower() == name.lower():
                return g
        return self.create_assignment_group(cid, name)

    def create_assignment(self, cid, **fields):
        a = {"id": self._id(), **fields, "html_url": "https://x/a"}
        self._assignments.append(a)
        self.writes.append(("create_assignment", fields.get("name"), fields.get("published")))
        return dict(a)

    def update_assignment_content(self, cid, aid, **fields):
        a = next(x for x in self._assignments if x["id"] == aid)
        a.update(fields)
        self.writes.append(("update_assignment", fields.get("name")))
        return dict(a)

    def create_discussion(self, cid, title, message, published=False, assignment=None, **extra):
        d = {"id": self._id(), "title": title, "message": message, "published": published,
             "assignment": {"id": self._id()} if assignment else None, "html_url": "https://x/d"}
        self._discussions.append(d)
        self.writes.append(("create_discussion", title, published))
        return dict(d)

    def update_discussion(self, cid, tid, **fields):
        d = next(x for x in self._discussions if x["id"] == tid)
        d.update({k: v for k, v in fields.items() if k != "assignment"})
        if fields.get("assignment"):
            d["assignment"] = d.get("assignment") or {"id": self._id()}
        self.writes.append(("update_discussion", fields.get("title")))
        return dict(d)

    def create_quiz(self, cid, **fields):
        q = {"id": self._id(), **fields, "html_url": "https://x/q"}
        self._quizzes.append(q)
        self.writes.append(("create_quiz", fields.get("title"), fields.get("published")))
        return dict(q)

    def update_quiz_content(self, cid, qid, **fields):
        q = next(x for x in self._quizzes if x["id"] == qid)
        q.update(fields)
        self.writes.append(("update_quiz", tuple(sorted(fields))))
        return dict(q)

    def create_quiz_question(self, cid, qid, question):
        self._questions.setdefault(qid, []).append({"id": self._id(), **question})
        self.writes.append(("create_question", question.get("question_type")))
        return {"id": self._next}

    def delete_quiz_question(self, cid, qid, qqid):
        self._questions[qid] = [q for q in self._questions.get(qid, []) if q["id"] != qqid]
        self.writes.append(("delete_question", qqid))

    def create_module(self, cid, name, position=None, published=None):
        m = {"id": self._id(), "name": name, "position": position, "published": published}
        self._modules.append(m)
        self.writes.append(("create_module", name, published))
        return dict(m)

    def delete_module(self, cid, mid):
        self._modules = [m for m in self._modules if m["id"] != mid]
        self.writes.append(("delete_module", mid))

    def add_module_item(self, cid, mid, item):
        self.writes.append(("add_item", mid, item.get("type"), item.get("page_url") or item.get("content_id")))
        return {"id": self._id(), **item}

    def create_rubric(self, cid, title, criteria, association=None, free_form=False):
        r = {"id": self._id(), "title": title, "data": criteria}
        self._rubrics.append(r)
        self.writes.append(("create_rubric", title))
        return {"rubric": dict(r)}

    def update_rubric(self, cid, rid, title, criteria, association=None, free_form=False):
        r = next(x for x in self._rubrics if x["id"] == rid)
        r.update(title=title, data=criteria)
        self.writes.append(("update_rubric", title))
        return {"rubric": dict(r)}


class TempCourse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.course_dir = Path(self.tmp.name) / "12345"
        self.build = BuildDir(self.course_dir, "12345")
        self.root = self.build.root

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def write_project_files(self):
        self.write("syllabus.html", "<div><h2>Syllabus</h2><p>Policies.</p></div>")
        self.write("home.html", hero_page("Home"))
        self.write("week1.html", hero_page("Week 1: Kickoff"))
        self.write("assign-gdd.html", "<div><h2>Game Design Document</h2><p>Write it.</p></div>")
        self.write("standup.html", "<div><h2>Standup</h2><p>Post.</p></div>")
        self.write("final.html", "<div><h2>Final Exam</h2><p>Sixty minutes.</p></div>")


# ------------------------------------------------------------ check_style
class CheckStyleTests(unittest.TestCase):
    def test_good_body_passes(self):
        rep = check_style.check(GOOD_HTML, palette=check_style.brand_palette())
        self.assertTrue(rep["ok"], rep["failed"])
        states = {c["id"]: c["state"] for c in rep["chips"]}
        for cid in ("one_h2", "heading_order", "alt", "ascii", "contrast", "sanitizer", "palette"):
            self.assertEqual(states.get(cid), "pass", cid)

    def test_bad_body_fails_on_every_rule(self):
        rep = check_style.check(BAD_HTML)
        self.assertFalse(rep["ok"])
        joined = "\n".join(rep["failed"])
        for needle in ("found 2 <h2>", "<ol>", "<br>", "<em>", "<style>", "class=", "id=",
                       "nested <ul>", "no <th>", "bare filename", "no alt attribute",
                       "no visible text", "contrast", "skipped level"):
            self.assertIn(needle, joined, needle)
        states = {c["id"]: c["state"] for c in rep["chips"]}
        self.assertEqual(states["one_h2"], "fail")
        self.assertEqual(states["contrast"], "fail")

    def test_non_ascii_is_a_warning_by_default_and_a_failure_when_asked(self):
        body = GOOD_HTML.replace("Why loops", "Why loops \u2014 really")
        self.assertTrue(check_style.check(body)["ok"])
        self.assertFalse(check_style.check(body, ascii_fails=True)["ok"])

    def test_off_palette_colour_is_flagged(self):
        body = GOOD_HTML.replace("#236192", "#ff00ff").replace('color: #ffffff;">Week', 'color: #ff00ff;">Week')
        rep = check_style.check(body, palette=check_style.brand_palette())
        self.assertTrue(any("off-palette" in w for w in rep["warned"]))

    def test_contrast_math(self):
        self.assertAlmostEqual(check_style.contrast_ratio("#000000", "#ffffff"), 21.0, places=1)
        self.assertGreater(check_style.contrast_ratio("#ffffff", "#061E3F"), 4.5)


# ------------------------------------------------------------- check_quiz
class CheckQuizTests(unittest.TestCase):
    def test_good_questions_pass_in_every_shape(self):
        manifest_q = PROJECT_MANIFEST["quizzes"][0]["questions"]
        rep = check_quiz.check(manifest_q)
        self.assertTrue(rep["ok"], rep["failed"])
        self.assertEqual(rep["count"], 3)
        self.assertEqual(rep["points"], 10)
        toolkit_q = [{"name": "Q1", "text": "<p>Pick one</p>",
                      "answers": [["A", 100], ["B", 0], ["C", 0], ["D", 0]]}]
        self.assertTrue(check_quiz.check(toolkit_q)["ok"])
        write_q = [check_quiz.to_write_shape(q, i) for i, q in enumerate(manifest_q, 1)]
        self.assertTrue(check_quiz.check(write_q)["ok"])
        self.assertEqual(write_q[2]["answers"][0], {"answer_text": "Variable ratio", "answer_weight": 100})

    def test_bad_questions_fail(self):
        bad = [
            {"text": "No correct", "type": "multiple_choice_question", "points": 1,
             "answers": [{"text": "a", "correct": False}, {"text": "b", "correct": False}]},
            {"text": "Two correct", "type": "multiple_choice_question", "points": 1,
             "answers": [{"text": "a", "correct": True}, {"text": "b", "correct": True}]},
            {"text": "", "type": "true_false_question", "points": 1,
             "answers": [{"text": "True", "correct": True}]},
            {"text": "Bad type", "type": "bogus_question", "points": 1, "answers": []},
        ]
        rep = check_quiz.check(bad, expect_count=3)
        self.assertFalse(rep["ok"])
        joined = "\n".join(rep["failed"])
        for needle in ("no answer is marked correct", "2 answers marked correct", "text is empty",
                       "exactly 2 answers", "unknown question type", "question count is 4"):
            self.assertIn(needle, joined, needle)

    def test_essay_needs_no_answers(self):
        rep = check_quiz.check([{"text": "Explain.", "type": "essay_question", "points": 10}])
        self.assertTrue(rep["ok"])
        self.assertNotIn("answers", check_quiz.to_write_shape({"text": "Explain.", "type": "essay_question"}, 1))


class QuestionMappingTests(unittest.TestCase):
    def test_read_to_write_maps_answer_fields(self):
        read = {"question_name": "Q1", "question_text": "Pick", "question_type": "multiple_choice_question",
                "points_possible": 2, "id": 99, "quiz_id": 5,
                "answers": [{"id": 1, "text": "A", "weight": 100, "comments": "yes"},
                            {"id": 2, "text": "B", "weight": 0, "html": "<p>B</p>"}]}
        write = ContentOps.question_read_to_write(read)
        self.assertNotIn("id", write)
        self.assertNotIn("quiz_id", write)
        self.assertEqual(write["answers"][0], {"answer_text": "A", "answer_weight": 100, "answer_comments": "yes"})
        self.assertEqual(write["answers"][1]["answer_html"], "<p>B</p>")
        self.assertEqual(write["answers"][1]["answer_weight"], 0)
        self.assertTrue(check_quiz.check([write])["ok"])

    def test_matching_left_right(self):
        read = {"question_text": "Match", "question_type": "matching_question",
                "answers": [{"left": "cat", "right": "meow", "text": "cat"}]}
        write = ContentOps.question_read_to_write(read)
        self.assertEqual(write["answers"][0]["answer_match_left"], "cat")
        self.assertEqual(write["answers"][0]["answer_match_right"], "meow")


# --------------------------------------------------------------- manifest
class ManifestTests(unittest.TestCase):
    def test_detects_modes(self):
        self.assertEqual(mf.detect_mode(PROJECT_MANIFEST), "project")
        self.assertEqual(mf.detect_mode(PAGES_MANIFEST), "pages")
        self.assertEqual(mf.detect_mode({"mode": "pages", "pages": []}), "pages")

    def test_valid_manifests_have_no_problems(self):
        self.assertEqual(mf.validate(PROJECT_MANIFEST), [])
        self.assertEqual(mf.validate(PAGES_MANIFEST), [])
        s = mf.summary(PROJECT_MANIFEST)
        self.assertEqual((s["pages"], s["assignments"], s["quizzes"], s["modules"], s["questions"]),
                         (2, 1, 1, 2, 3))
        self.assertTrue(s["syllabus"])

    def test_dangling_references_and_duplicates_are_reported(self):
        bad = json.loads(json.dumps(PROJECT_MANIFEST))
        bad["modules"][1]["items"].append({"type": "Assignment", "key": "nope"})
        bad["modules"][1]["items"].append({"type": "Page", "slug": "missing"})
        bad["modules"][1]["items"].append({"type": "Video"})
        bad["pages"].append({"slug": "home", "title": "Home again", "file": "x.html", "front_page": True})
        bad["assignments"].append({"key": "gdd", "name": "Dup", "file": "y.html",
                                   "submission_types": ["telepathy"], "points": "lots"})
        bad["quizzes"][0]["questions"] = []
        problems = mf.validate(bad)
        joined = "\n".join(problems)
        for needle in ("assignment key 'nope'", "page slug 'missing'", "unknown type 'Video'",
                       "slug 'home' appears twice", "Only one page can be the front page",
                       "key 'gdd' appears twice", "unknown submission types", "not a number",
                       "has no questions"):
            self.assertIn(needle, joined, needle)

    def test_pages_manifest_needs_module_and_body(self):
        problems = mf.validate({"pages": [{"title": "X"}]})
        self.assertTrue(any("no file or html" in p for p in problems))
        self.assertTrue(any("names no module" in p for p in problems))

    def test_inline_html_and_root(self):
        self.assertEqual(mf.body_of({"html": "<p>x</p>"}, Path(".")), "<p>x</p>")
        self.assertEqual(mf.resolve_root({"root": "C:/somewhere"}, Path("build")), Path("C:/somewhere"))
        self.assertEqual(mf.resolve_root({}, Path("build")), Path("build"))


# ----------------------------------------------------------- verify_slots
class VerifySlotsTests(TempCourse):
    def test_project_fixture_verifies(self):
        self.write_project_files()
        result = verify_slots.verify(PROJECT_MANIFEST, self.root)
        self.assertTrue(result["ok"], [r for r in result["rows"] if r["problems"]])
        kinds = [r["kind"] for r in result["rows"]]
        self.assertEqual(kinds, ["page", "page", "syllabus", "assignment", "discussion", "quiz"])
        quiz = result["rows"][-1]
        self.assertEqual(quiz["questions"], 3)
        self.assertEqual(quiz["points"], 10)

    def test_missing_wrong_hero_two_h2_nonascii_and_bad_quiz_fail(self):
        self.write_project_files()
        self.write("home.html", hero_page("Some Other Lesson"))                 # wrong slot
        self.write("week1.html", hero_page("Week 1: Kickoff") + "<h2>Again</h2>")  # two h2
        self.write("assign-gdd.html", "<div><p>Caf\u00e9</p></div>")                # non-ASCII
        os.remove(self.root / "standup.html")                                        # missing
        bad = json.loads(json.dumps(PROJECT_MANIFEST))
        bad["quizzes"][0]["questions"][2]["answers"][1]["correct"] = True
        result = verify_slots.verify(bad, self.root)
        self.assertFalse(result["ok"])
        by_key = {r["key"]: r for r in result["rows"]}
        self.assertIn("does not match the slot title", by_key["home"]["problems"][0])
        self.assertIn("2 <h2>", by_key["week-1"]["problems"][0])
        self.assertIn("non-ASCII", by_key["gdd"]["problems"][0])
        self.assertIn("missing file", by_key["standup-1"]["problems"][0])
        self.assertTrue(any("marked correct" in p for p in by_key["final"]["problems"]))
        self.assertEqual(result["failed"], 5)

    def test_syllabus_title_rule_and_empty_body(self):
        self.assertTrue(verify_slots.title_matches("Course Syllabus", "IMT 1213 Syllabus and Policies"))
        self.assertTrue(verify_slots.title_matches("Week 3: Loops", "Assignment - Week 3: Loops"))
        self.assertFalse(verify_slots.title_matches("Week 3: Loops", "Week 4: Functions"))
        self.write("empty.html", "<div><p>&nbsp;</p></div>")
        result = verify_slots.verify({"pages": [{"key": "e", "title": "Empty", "file": "empty.html",
                                                 "module": "M"}]}, self.root)
        self.assertIn("body is empty", result["rows"][0]["problems"][0])

    def test_cli_exit_code(self):
        self.write_project_files()
        path = self.root / "manifest.json"
        path.write_text(json.dumps(PROJECT_MANIFEST), encoding="utf-8")
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify_slots.main([str(path), "--root", str(self.root)]), 0)
            os.remove(self.root / "home.html")
            self.assertEqual(verify_slots.main([str(path), "--root", str(self.root)]), 2)


# -------------------------------------------------------------- push_pages
class PushPagesTests(TempCourse):
    def setUp(self):
        super().setUp()
        self.write("week3.html", hero_page("Week 3: Loops"))
        self.write("week4.html", hero_page("Week 4: Functions"))

    def test_plan_is_a_dry_run(self):
        client = FakeClient(modules=[{"id": 7, "name": "Week 3"}])
        st = statemod.empty("12345")
        st["pages"]["n2"] = {"url": "week-4-functions", "page_id": 4, "title": "Week 4: Functions"}
        plan = push_pages.plan(client, "12345", PAGES_MANIFEST, self.root, st, publish=False)
        self.assertEqual(client.writes, [])
        actions = {r["key"]: r["action"] for r in plan["pages"]}
        self.assertEqual(actions, {"n1": "create", "n2": "update", "n3": "skip"})
        mods = {m["name"]: m["action"] for m in plan["modules"]}
        self.assertEqual(mods, {"Week 3": "reuse", "Week 4": "create", "Week 5": "create"})
        self.assertEqual(plan["counts"], {"create": 1, "update": 1, "skip": 1, "modules_new": 2})
        self.assertIn("unpublished", plan["sentence"])
        self.assertEqual(plan["keys"], ["n1", "n2"])

    def test_apply_creates_updates_places_and_trusts_the_response_slug(self):
        client = FakeClient(modules=[{"id": 7, "name": "Week 3"}])
        client._pages["old-slug"] = {"url": "old-slug", "page_id": 4, "title": "Old", "body": ""}

        def update_page(cid, slug, body=None, title=None, published=None, front_page=None):
            # Canvas regenerates the slug when the title changes (gotcha 7).
            client.writes.append(("update_page", slug, published))
            client._pages.pop(slug, None)
            client._pages["week-4-functions"] = {"url": "week-4-functions", "page_id": 4, "title": title,
                                                 "body": body, "published": published}
            return dict(client._pages["week-4-functions"])
        client.update_page = update_page
        st = statemod.empty("12345")
        st["pages"]["n2"] = {"url": "old-slug", "page_id": 4, "title": "Old"}
        saved = []
        result = push_pages.apply(client, "12345", PAGES_MANIFEST, self.root, st, publish=False,
                                  on_state=lambda s: saved.append(json.dumps(s)))
        self.assertEqual((result["created"], result["updated"], result["skipped"]), (1, 1, 1))
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(st["pages"]["n2"]["url"], "week-4-functions")
        kinds = [w[0] for w in client.writes]
        self.assertIn("create_page", kinds)
        self.assertIn("update_page", kinds)
        self.assertEqual(kinds.count("create_module"), 1)       # Week 4 only; Week 3 reused, Week 5 skipped
        items = [w for w in client.writes if w[0] == "add_item"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][1], 7)                          # placed in the existing Week 3 module
        self.assertTrue(all(w[2] is False for w in client.writes if w[0] in ("create_page", "update_page")))
        self.assertEqual(len(saved), 2)

    def test_state_file_of_another_course_is_refused(self):
        self.build.write_json(self.build.state_path, {"course_id": "999", "pages": {}})
        with self.assertRaises(ValueError):
            statemod.load(self.build)


# ------------------------------------------------------------ push_project
class ModuleGateTests(TempCourse):
    def test_refuses_when_modules_exist_and_no_owning_state(self):
        client = FakeClient(modules=[{"id": 1, "name": "Week 1"}, {"id": 2, "name": "Week 2"}])
        with self.assertRaises(push_project.ModuleWipeRefused) as ctx:
            push_project.module_gate(client, "12345", statemod.empty("12345"))
        msg = str(ctx.exception)
        self.assertIn("already has 2 modules", msg)
        self.assertIn("Nothing has been written", msg)
        self.assertNotIn("--", msg)
        self.assertEqual(client.writes, [])
        facts = ctx.exception.facts
        self.assertTrue(facts["refused"])
        self.assertEqual(facts["modules"], 2)

    def test_state_of_another_course_does_not_count(self):
        client = FakeClient(modules=[{"id": 1, "name": "Week 1"}])
        foreign = statemod.empty("999")
        foreign["built_modules"] = True
        with self.assertRaises(push_project.ModuleWipeRefused):
            push_project.module_gate(client, "12345", foreign)

    def test_passes_when_owned_or_empty_or_forced(self):
        client = FakeClient(modules=[{"id": 1, "name": "Week 1"}])
        owned = statemod.empty("12345")
        owned["built_modules"] = True
        self.assertFalse(push_project.module_gate(client, "12345", owned)["refused"])
        self.assertFalse(push_project.module_gate(FakeClient(), "12345", statemod.empty("12345"))["refused"])
        forced = push_project.module_gate(client, "12345", statemod.empty("12345"), rebuild_modules=True)
        self.assertFalse(forced["refused"])
        self.assertIn("deleted and rebuilt", forced["sentence"])
        skipped = push_project.module_gate(client, "12345", statemod.empty("12345"), skip_modules=True)
        self.assertTrue(skipped["skipped"])

    def test_apply_refuses_before_any_write(self):
        self.write_project_files()
        client = FakeClient(modules=[{"id": 1, "name": "Week 1"}])
        with self.assertRaises(push_project.ModuleWipeRefused):
            push_project.apply(client, "12345", PROJECT_MANIFEST, self.root, statemod.empty("12345"), False)
        self.assertEqual(client.writes, [])


class PushProjectTests(TempCourse):
    def test_plan_reports_create_update_and_gate(self):
        self.write_project_files()
        client = FakeClient(modules=[{"id": 1, "name": "Old"}],
                            pages=[{"url": "home", "title": "Home"}],
                            assignments=[{"id": 5, "name": "Game Design Document"}])
        plan = push_project.plan(client, "12345", PROJECT_MANIFEST, self.root, statemod.empty("12345"))
        self.assertEqual(client.writes, [])
        self.assertTrue(plan["gate"]["refused"])
        by = {(r["kind"], r["key"]): r["action"] for r in plan["rows"]}
        self.assertEqual(by[("page", "home")], "update")
        self.assertEqual(by[("page", "week-1")], "create")
        self.assertEqual(by[("assignment", "gdd")], "update")
        self.assertEqual(by[("quiz", "final")], "create")
        self.assertEqual(len(plan["modules"]), 2)

    def test_apply_builds_everything_unpublished_and_records_state(self):
        self.write_project_files()
        client = FakeClient()
        st = statemod.empty("12345")
        result = push_project.apply(client, "12345", PROJECT_MANIFEST, self.root, st, publish=False,
                                    backup_dir=self.build.backups_dir)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(len(result["pages"]), 2)
        self.assertTrue(result["syllabus"])
        self.assertEqual(len(result["quizzes"]), 1)
        self.assertEqual(len(result["modules"]), 2)
        kinds = [w[0] for w in client.writes]
        # quiz created unpublished, description in its own PUT, questions as JSON, never published
        self.assertIn(("create_quiz", "Final Exam", False), client.writes)
        self.assertIn(("update_quiz", ("description",)), client.writes)
        self.assertEqual(kinds.count("create_question"), 3)
        self.assertNotIn(("update_quiz", ("published",)), client.writes)
        # front page -> default_view=wiki; syllabus written and backed up
        self.assertIn(("update_course", ("default_view",)), client.writes)
        self.assertIn(("update_course", ("syllabus_body",)), client.writes)
        self.assertTrue(list(self.build.backups_dir.glob("syllabus-*.html")))
        # groups: "Assignments" reused, "Final Exam" created
        self.assertEqual([w[1] for w in client.writes if w[0] == "create_group"], ["Final Exam"])
        # modules rebuilt with every item resolved
        items = [w for w in client.writes if w[0] == "add_item"]
        self.assertEqual(len(items), 6)
        self.assertEqual({w[2] for w in items}, {"Page", "SubHeader", "Assignment", "Discussion", "Quiz"})
        self.assertTrue(statemod.owns(st, "12345"))
        self.assertEqual(st["quizzes"]["final"], result["quizzes"][0]["id"])
        # a second run is idempotent: passes the gate, updates instead of creating
        client.writes.clear()
        push_project.apply(client, "12345", PROJECT_MANIFEST, self.root, st, publish=True)
        kinds = [w[0] for w in client.writes]
        self.assertNotIn("create_assignment", kinds)
        self.assertIn("update_assignment", kinds)
        self.assertEqual(kinds.count("delete_question"), 3)
        self.assertIn(("update_quiz", ("published",)), client.writes)
        self.assertEqual(len(client._modules), 2)

    def test_bad_quiz_stops_before_creating_it(self):
        self.write_project_files()
        bad = json.loads(json.dumps(PROJECT_MANIFEST))
        bad["quizzes"][0]["questions"][0]["answers"] = []
        client = FakeClient()
        with self.assertRaises(ValueError):
            push_project.apply(client, "12345", bad, self.root, statemod.empty("12345"), False)
        self.assertNotIn("create_quiz", [w[0] for w in client.writes])


# ----------------------------------------------------------------- rubrics
class RubricsTests(unittest.TestCase):
    ENTRIES = [{"assignment": "Final Submission", "title": "Final project rubric", "use_for_grading": True,
                "criteria": [{"description": "Gameplay", "points": 40,
                              "ratings": [{"description": "Excellent", "points": 40},
                                          {"description": "Missing", "points": 0}]},
                             {"description": "Docs", "points": 10,
                              "ratings": [{"description": "Good", "points": 8}]}]},
               {"assignment": "Nope", "title": "Orphan", "criteria": [{"description": "x", "points": 1,
                                                                        "ratings": [{"description": "y", "points": 1}]}]}]

    def test_plan_and_apply(self):
        client = FakeClient(assignments=[{"id": 42, "name": "Final Submission", "points_possible": 50}])
        plan = rubrics.plan(client, "1", self.ENTRIES)
        self.assertEqual([r["action"] for r in plan["rows"]], ["create", "skip"])
        self.assertEqual(plan["rows"][0]["points"], 50)
        self.assertTrue(any("max rating 8" in w for w in plan["rows"][0]["warnings"]))
        self.assertIn("not found", plan["rows"][1]["warnings"][0])
        self.assertEqual(client.writes, [])
        result = rubrics.apply(client, "1", self.ENTRIES)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(client.writes, [("create_rubric", "Final project rubric")])
        # idempotent by title
        plan2 = rubrics.plan(client, "1", self.ENTRIES)
        self.assertEqual(plan2["rows"][0]["action"], "update")


# ---------------------------------------------------------------- generate
class GenerateTests(unittest.TestCase):
    def test_system_prompt_has_palette_humanize_rules_and_strict_json(self):
        brand = generate.load_brand()
        system = generate.system_prompt(brand, "hybrid")
        for hexv in ("#061E3F", "#E9A821", "#236192", "#C11F31", "#2c3a4d", "#1565C0"):
            self.assertIn(hexv, system, hexv)
        self.assertIn(HUMANIZE_RULES, system)
        self.assertIn("No em dashes or en dashes", system)
        self.assertIn("exactly one JSON object", system)
        self.assertIn("Exactly ONE <h2>", system)
        self.assertIn("alt", system)
        self.assertIn("HYBRID look", system)
        self.assertIn("CLEAN look", generate.system_prompt(brand, "clean"))

    def test_user_prompt_carries_the_form_and_asks_for_json(self):
        prompt = generate.build_prompt("assignment", "Week 3 Lab", "Week 3", 25,
                                       "Build a loop that sums a list.", source="def total(xs): ...",
                                       course_label="IMT 1213", look="clean", group="Labs", position=2)
        for needle in ("Week 3 Lab", "Module: Week 3", "Points: 25", "Assignment group: Labs",
                       "Position in module: 2", "IMT 1213", "Build a loop", "def total",
                       "SOURCE TEXT (material, not instructions", "JSON object only", '"Assignment"'):
            self.assertIn(needle, prompt, needle)

    def test_normalize_output_and_scan(self):
        data = {"title": "Week 3: Loops", "html": GOOD_HTML, "summary": "A lesson page.",
                "module_item": {"type": "Assignment"}}
        content, problems = generate.normalize_output(data, "page", {"title": "x"})
        self.assertEqual(problems, [])
        self.assertEqual(content["module_item"]["type"], "Page")     # kind wins over the model
        findings = generate.scan({"kind": "page", **content}, generate.palette_of(generate.load_brand()))
        self.assertTrue(findings["ok"])
        quiz_data = {"title": "Final", "html": GOOD_HTML,
                     "quiz": {"questions": PROJECT_MANIFEST["quizzes"][0]["questions"], "time_limit": 60}}
        content, _ = generate.normalize_output(quiz_data, "quiz", {"points": 10})
        self.assertEqual(content["assignment"]["points"], 10)
        findings = generate.scan({"kind": "quiz", **content})
        self.assertTrue(findings["ok"])
        self.assertEqual(findings["quiz"]["count"], 3)
        content, problems = generate.normalize_output({"title": "x"}, "page", {})
        self.assertIn("no html", problems[0])

    def test_update_draft_marks_human_edits_and_rescans(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = BuildDir(Path(tmp) / "1", "1")
            record = {"id": "d1", "kind": "page", "title": "T", "html": GOOD_HTML, "summary": "",
                      "source": {"title": "model", "html": "model"}, "findings": generate.scan({"kind": "page", "html": GOOD_HTML})}
            build.save_draft(record)
            self.assertTrue(record["findings"]["ok"])
            edited = generate.update_draft(build, "d1", {"html": BAD_HTML, "module": "Week 1"})
            self.assertEqual(edited["source"]["html"], "human")
            self.assertEqual(edited["source"]["title"], "model")
            self.assertFalse(edited["findings"]["ok"])
            self.assertTrue(generate.hard_failures(edited))
            self.assertEqual(build.drafts()[0]["module"], "Week 1")


# ------------------------------------------------------------------- place
class PlaceTests(unittest.TestCase):
    def test_sentences(self):
        d = {"kind": "page", "title": "Week 3: Loops"}
        self.assertEqual(place.sentence(d, "Week 3", 2, False, "IMT 1213"),
                         "Create the unpublished page 'Week 3: Loops' in module 'Week 3' at position 2 in IMT 1213. "
                         "Existing content is not touched.")
        q = {"kind": "quiz", "title": "Final", "quiz": {"questions": [{}, {}]}, "assignment": {"points": 10}}
        self.assertIn("quiz 'Final' with 2 questions worth 10 points", place.sentence(q, None, None, True, "C"))
        self.assertIn("published quiz", place.sentence(q, None, None, True, "C"))
        self.assertIn("Replace the syllabus", place.sentence({"kind": "syllabus", "title": "S"}, None, None, False, "C"))

    def test_place_refuses_a_failing_draft_and_places_a_good_one(self):
        client = FakeClient(modules=[{"id": 7, "name": "Week 3"}])
        bad = {"kind": "page", "title": "X", "html": BAD_HTML}
        with self.assertRaises(ValueError):
            place.place(client, "https://c", "1", bad, module_id=7, position=1)
        self.assertEqual(client.writes, [])
        good = {"kind": "page", "title": "Week 3: Loops", "html": GOOD_HTML}
        out = place.place(client, "https://c", "1", good, module_id=7, position=2, publish=False)
        self.assertEqual(out["mismatches"], [])
        self.assertEqual(out["url"], "week-3-loops")
        self.assertIsNotNone(out["item_id"])
        self.assertEqual(client.writes[0], ("create_page", "week-3-loops", False))
        self.assertEqual(client.writes[1][:3], ("add_item", 7, "Page"))

    def test_place_quiz_posts_questions_and_stays_unpublished(self):
        client = FakeClient()
        draft = {"kind": "quiz", "title": "Final", "html": GOOD_HTML,
                 "quiz": {"questions": PROJECT_MANIFEST["quizzes"][0]["questions"], "time_limit": 60},
                 "assignment": {"points": 10}, "group": "Final Exam"}
        out = place.place(client, "https://c", "1", draft, module_id=None, publish=False)
        kinds = [w[0] for w in client.writes]
        self.assertEqual(kinds.count("create_question"), 3)
        self.assertIn(("create_quiz", "Final", False), client.writes)
        self.assertNotIn(("update_quiz", ("published",)), client.writes)
        self.assertEqual(out["questions"], 3)
        self.assertEqual(out["mismatches"], [])


# ------------------------------------------------------------ diff_content
class DiffContentTests(unittest.TestCase):
    def test_lost_words_show_up(self):
        a = "<p>The loop runs until the counter reaches ten.</p>"
        b = "<div><p>The loop runs until the counter reaches</p></div>"
        result = diff_content.diff(a, b)
        self.assertEqual([r["word"] for r in result["lost"]], ["ten"])
        self.assertEqual(result["gained"], [])


if __name__ == "__main__":
    unittest.main()
