"""Tests for the Course tools area. Standard library only.

Run:  python -m unittest tests.test_courseops

Every Canvas call goes to a fake client that records what was written, so
nothing here touches a network. The properties under test are the ones a live
course depends on:

  - the academic calendar parses out of the knowledge file, breaks and all
  - the due-date table steps over a full break week, moves off a holiday, and
    lands the final on the last day of finals
  - a Thursday start and the following Monday start give the same table
  - an import into a destination that already has content is refused, and the
    refusal survives until someone says add anyway
  - the nav plan hides what is not on the keep-list and never touches Settings
  - a quiz backup maps the READ shape of an answer to the WRITE shape and is
    always created unpublished
  - the SLO validator rejects an invented outcome id, and every other way an
    alignment can hand-wave
"""
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from courseforge.courseops import (calendar as cal, due_dates, export_import,  # noqa: E402
                                   nav, quiz_backup, slo)
from courseforge.courseops.common import Refused  # noqa: E402

# Fall 2026, from knowledge/academic-calendar.md: a Thursday face-to-face start,
# a Monday online start, Labor Day, a two-day Fall Break, a full Thanksgiving
# week off, and finals ending on the eleventh of December.
F26_F2F = "2026-08-20"
F26_ONLINE = "2026-08-24"
F26_FINALS = "2026-12-11"
F26_BREAKS = ["2026-09-07", "2026-10-12..2026-10-13", "2026-11-23..2026-11-27"]


# ------------------------------------------------------------------- fakes
class FakeCfg:
    base_url = "https://example.instructure.com"
    allow_canvas_writes = True
    model = "opus"


class FakeApp:
    """The slice of App the modules use: a config, a content client and a place
    to keep files."""

    def __init__(self, client, root):
        self.cfg = FakeCfg()
        self.content = client
        self.root = Path(root)

    def course_dir(self, course_id) -> Path:
        path = self.root / str(course_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


class FakeContent:
    """Records every write. Reads answer from whatever the test set up."""

    def __init__(self, **state):
        self.state = {"course": {"id": 101, "name": "Test course", "course_code": "IMT 1213"},
                      "counts": {}, "tabs": [], "quizzes": [], "questions": {}, "modules": [],
                      "assignments": [], "discussions": [], "pages": []}
        self.state.update(state)
        self.writes = []
        self.created_quizzes = []
        self.created_questions = []

    # -- reads
    def course_detail(self, course_id, include=None):
        return dict(self.state["course"])

    def course_counts(self, course_id):
        return dict(self.state["counts"])

    def tabs(self, course_id):
        return [dict(t) for t in self.state["tabs"]]

    def modules(self, course_id, include_items=False):
        return [dict(m) for m in self.state["modules"]]

    def module_items(self, course_id, module_id):
        for m in self.state["modules"]:
            if m.get("id") == module_id:
                return list(m.get("items") or [])
        return []

    def assignments_content(self, course_id):
        return [dict(a) for a in self.state["assignments"]]

    def quizzes_content(self, course_id):
        return [dict(q) for q in self.state["quizzes"]]

    def discussions(self, course_id, announcements=False):
        return [dict(d) for d in self.state["discussions"]]

    def pages(self, course_id):
        return [dict(p) for p in self.state["pages"]]

    def quiz_content(self, course_id, quiz_id):
        for q in self.state["quizzes"]:
            if str(q.get("id")) == str(quiz_id):
                return dict(q)
        return {}

    def quiz_questions(self, course_id, quiz_id):
        return [dict(q) for q in self.state["questions"].get(str(quiz_id), [])]

    def get(self, path, **params):
        return {}

    # -- writes
    def update_tab(self, course_id, tab_id, hidden=None, position=None):
        self.writes.append(("tab", tab_id, {"hidden": hidden, "position": position}))
        for t in self.state["tabs"]:
            if str(t.get("id")) == str(tab_id):
                if hidden is not None:
                    t["hidden"] = bool(hidden)
                if position is not None:
                    t["position"] = position
                return dict(t)
        return {}

    def create_quiz(self, course_id, **fields):
        new_id = 900 + len(self.created_quizzes)
        self.created_quizzes.append(dict(fields))
        quiz = {"id": new_id, "html_url": f"https://x/quizzes/{new_id}", **fields}
        self.state["quizzes"].append(quiz)
        self.state["questions"][str(new_id)] = []
        return quiz

    def create_quiz_question(self, course_id, quiz_id, question):
        self.created_questions.append(question)
        stored = dict(question)
        stored.setdefault("points_possible", question.get("points_possible"))
        self.state["questions"].setdefault(str(quiz_id), []).append(stored)
        return stored

    def update_assignment_content(self, course_id, assignment_id, **fields):
        self.writes.append(("assignment", assignment_id, fields))
        return {"id": assignment_id, **fields}

    def update_quiz_content(self, course_id, quiz_id, **fields):
        self.writes.append(("quiz", quiz_id, fields))
        return {"id": quiz_id, "published": False, **fields}


def refusing_gate(kind, payload, sentence, detail=None):
    """A gate that stands in for the person saying no. The sentence is what the
    confirm dialog would have shown."""
    raise Refused(sentence)


def allowing_gate(seen):
    def gate(kind, payload, sentence, detail=None):
        seen.append({"kind": kind, "payload": payload, "sentence": sentence, "detail": detail})
    return gate


# ================================================================= calendar
class CalendarTest(unittest.TestCase):
    def test_reads_fall_2026_out_of_the_knowledge_file(self):
        found = cal.lookup(F26_ONLINE)
        self.assertIsNotNone(found, "Fall 2026 is missing from knowledge/academic-calendar.md")
        self.assertEqual(found["term"], "Fall 2026")
        self.assertEqual(found["finals_end"], F26_FINALS)
        self.assertEqual(found["breaks"], F26_BREAKS)

    def test_term_name_follows_the_start_month(self):
        self.assertEqual(cal.term_name("2026-08-20"), "Fall 2026")
        self.assertEqual(cal.term_name("2027-01-12"), "Spring 2027")
        self.assertEqual(cal.term_name("2027-06-01"), "Summer 2027")

    def test_a_break_range_expands_to_every_day_in_it(self):
        days = cal.expand_breaks(["2026-11-23..2026-11-27"])
        self.assertEqual(len(days), 5)
        self.assertIn(date(2026, 11, 25), days)
        self.assertNotIn(date(2026, 11, 28), days)

    def test_typed_breaks_accept_the_spellings_people_use(self):
        self.assertEqual(cal.parse_breaks_text("2026-09-07, 2026-10-12 to 2026-10-13"),
                         ["2026-09-07", "2026-10-12..2026-10-13"])
        with self.assertRaises(ValueError):
            cal.parse_breaks_text("next Monday")

    def test_an_unknown_term_is_not_invented(self):
        self.assertIsNone(cal.lookup("2099-08-20"))


# ================================================================ due dates
class DueDateTest(unittest.TestCase):
    def table(self, start, weeks=15):
        return due_dates.compute(start, weeks, F26_FINALS, F26_BREAKS, "Monday", "23:59")

    def test_the_two_fall_2026_starts_give_one_table(self):
        self.assertEqual(self.table(F26_F2F), self.table(F26_ONLINE))

    def test_week_one_gets_a_whole_first_week(self):
        first = self.table(F26_ONLINE)[0]
        self.assertEqual(first["week"], 1)
        self.assertGreater((date.fromisoformat(first["due_date"])
                            - date.fromisoformat(F26_ONLINE)).days, 6)

    def test_a_due_day_that_is_a_holiday_moves_forward(self):
        rows = {r["due_date"]: r for r in self.table(F26_ONLINE)}
        self.assertNotIn("2026-09-07", rows, "Labor Day is a holiday and cannot be a due day")
        self.assertIn("2026-09-08", rows)
        self.assertIn("moved off a holiday", rows["2026-09-08"]["moved"])

    def test_no_week_is_due_inside_the_thanksgiving_week(self):
        for row in self.table(F26_ONLINE):
            self.assertFalse("2026-11-23" <= row["due_date"] <= "2026-11-27",
                             f"week {row['week']} is due during the Thanksgiving break")

    def test_a_full_break_week_is_stepped_over(self):
        moved = [r for r in self.table(F26_ONLINE) if "steps over a full break week" in r["moved"]]
        self.assertTrue(moved, "the Thanksgiving week should be stepped over")

    def test_the_final_lands_on_the_last_day_of_finals(self):
        last = self.table(F26_ONLINE)[-1]
        self.assertEqual(last["due_date"], F26_FINALS)
        self.assertEqual(last["due_at"], F26_FINALS + "T23:59:00")
        self.assertIn("final", last["moved"])

    def test_every_week_appears_once_and_in_order(self):
        rows = self.table(F26_ONLINE)
        self.assertEqual([r["week"] for r in rows], list(range(1, 16)))
        dates = [r["due_date"] for r in rows]
        self.assertEqual(dates, sorted(dates))

    def test_a_chosen_weekday_and_time_are_honoured(self):
        rows = due_dates.compute(F26_ONLINE, 4, F26_FINALS, F26_BREAKS, "Friday", "17:00")
        self.assertEqual(date.fromisoformat(rows[0]["due_date"]).weekday(), 4)
        self.assertTrue(rows[0]["due_at"].endswith("T17:00:00"))

    def test_a_bad_weekday_or_time_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            due_dates.compute(F26_ONLINE, 4, F26_FINALS, F26_BREAKS, "Someday", "23:59")
        with self.assertRaises(ValueError):
            due_dates.compute(F26_ONLINE, 4, F26_FINALS, F26_BREAKS, "Monday", "25:00")

    def test_a_wall_clock_time_becomes_one_utc_instant(self):
        # Central Daylight Time is six hours behind UTC, so 23:59 on the fourteenth
        # is 04:59 on the fifteenth in UTC. tz_offset_min is the JavaScript sign.
        self.assertEqual(due_dates.to_utc_iso("2026-09-14T23:59:00", None, 300),
                         "2026-09-15T04:59:00Z")
        self.assertTrue(due_dates.same_instant("2026-09-15T04:59:00Z",
                                               "2026-09-15T04:59:00+00:00"))
        self.assertFalse(due_dates.same_instant("2026-09-15T04:59:00Z", None))

    def test_only_the_items_whose_date_would_change_are_written(self):
        rows = [{"week": 3, "due_local": "2026-09-14T23:59:00", "items": [
            {"kind": "assignment", "id": "1", "name": "Lab 3", "current": "2026-09-15T04:59:00Z"},
            {"kind": "quiz", "id": "2", "name": "Quiz 3", "current": None},
            {"kind": "assignment", "id": "3", "name": "Also in week 2", "current": None,
             "skip": "also in an earlier module; dated there"},
        ]}]
        writes = due_dates.build_writes(rows, None, 300)
        self.assertEqual([w["id"] for w in writes], ["2"])
        self.assertEqual(writes[0]["to"], "2026-09-15T04:59:00Z")

    def test_the_week_number_comes_out_of_the_module_name(self):
        self.assertEqual(due_dates.week_number("Week 7: Lighting"), 7)
        self.assertEqual(due_dates.week_number("WEEK12"), 12)
        self.assertIsNone(due_dates.week_number("Start Here"))


# =================================================================== import
class ImportGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def app(self, dest_counts, source_counts=None):
        client = FakeContent(counts=dest_counts)
        source = source_counts or {"pages": 41, "modules": 12, "assignments": 6,
                                   "quizzes": 0, "discussions": 0, "files": 3}

        real_counts = client.course_counts
        real_detail = client.course_detail

        def counts(course_id):
            return dict(source) if str(course_id) == "555" else real_counts(course_id)

        def detail(course_id, include=None):
            if str(course_id) == "555":
                return {"id": 555, "name": "Last term", "course_code": "IMT 1213"}
            return real_detail(course_id, include)

        client.course_counts = counts
        client.course_detail = detail
        return FakeApp(client, self.tmp.name)

    def test_a_populated_destination_is_refused_with_a_sentence_to_read(self):
        app = self.app({"pages": 9, "modules": 2, "assignments": 4, "quizzes": 0,
                        "discussions": 1, "files": 0})
        plan = export_import.import_plan(app, 101, source_course_id=555)
        self.assertTrue(plan["refusal"])
        self.assertIn("already has", plan["refusal"])
        self.assertIn("Add anyway", plan["refusal"])
        with self.assertRaises(Refused):
            export_import.import_course(app, 101, source_course_id=555, apply=True,
                                        gate=allowing_gate([]))
        self.assertEqual(app.content.writes, [], "nothing may be written on a refusal")

    def test_add_anyway_lifts_the_refusal_and_the_gate_still_asks(self):
        app = self.app({"pages": 9, "modules": 2, "assignments": 4, "quizzes": 0,
                        "discussions": 1, "files": 0})
        plan = export_import.import_plan(app, 101, source_course_id=555, force=True)
        self.assertIsNone(plan["refusal"])
        with self.assertRaises(Refused):
            export_import.import_course(app, 101, source_course_id=555, force=True, apply=True,
                                        gate=refusing_gate)

    def test_an_empty_destination_is_not_refused_and_the_sentence_reads_plainly(self):
        app = self.app({k: 0 for k in export_import.COUNT_KINDS})
        plan = export_import.import_plan(app, 101, source_course_id=555)
        self.assertIsNone(plan["refusal"])
        self.assertEqual(
            plan["sentence"],
            "Copy 41 pages, 12 modules, 6 assignments from Last term into Test course. "
            "Nothing in Test course is deleted; publish state is not changed.")

    def test_the_confirm_detail_says_what_each_count_becomes(self):
        app = self.app({"pages": 2, "modules": 0, "assignments": 0, "quizzes": 0,
                        "discussions": 0, "files": 0})
        plan = export_import.import_plan(app, 101, source_course_id=555, force=True)
        pages = next(d for d in plan["detail"] if d["label"] == "Pages")
        self.assertEqual(pages["from"], "2")
        self.assertEqual(pages["to"], "43 (adds 41)")

    def test_exactly_one_source_and_one_destination_are_required(self):
        app = self.app({})
        with self.assertRaises(ValueError):
            export_import.import_plan(app, 101)
        with self.assertRaises(ValueError):
            export_import.import_plan(app, 101, source_course_id=555, imscc="x.imscc")
        with self.assertRaises(ValueError):
            export_import.import_plan(app, 101, source_course_id=101)
        with self.assertRaises(FileNotFoundError):
            export_import.import_plan(app, 101, imscc=str(Path(self.tmp.name) / "nope.imscc"))


# ====================================================================== nav
TABS = [
    {"id": "home", "label": "Home", "position": 1, "hidden": False},
    {"id": "announcements", "label": "Announcements", "position": 2, "hidden": True},
    {"id": "assignments", "label": "Assignments", "position": 3, "hidden": False},
    {"id": "modules", "label": "Modules", "position": 4, "hidden": False},
    {"id": "quizzes", "label": "Quizzes", "position": 5, "hidden": False},
    {"id": "grades", "label": "Grades", "position": 6, "hidden": False},
    {"id": "settings", "label": "Settings", "position": 20, "hidden": False},
    {"id": "context_external_tool_99", "label": "Publisher", "position": 7, "hidden": False},
]
KEEP = ["home", "announcements", "modules", "grades", "context_external_tool_382357"]


class NavTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = FakeApp(FakeContent(tabs=[dict(t) for t in TABS]), self.tmp.name)

    def test_the_plan_follows_the_keep_list(self):
        plan = nav.plan(TABS, KEEP)
        actions = {r["id"]: r["action"] for r in plan["rows"]}
        self.assertEqual(actions["announcements"], "show")
        self.assertEqual(actions["assignments"], "hide")
        self.assertEqual(actions["quizzes"], "hide")
        self.assertEqual(actions["context_external_tool_99"], "hide")
        self.assertEqual(actions["modules"], "order")
        self.assertEqual(plan["visible_after"], ["Home", "Announcements", "Modules", "Grades"])

    def test_settings_is_never_touched_and_home_is_never_hidden(self):
        plan = nav.plan(TABS, ["modules"])
        by_id = {r["id"]: r for r in plan["rows"]}
        self.assertEqual(by_id["settings"]["action"], "leave")
        self.assertEqual(by_id["home"]["action"], "keep")
        self.assertFalse(by_id["home"]["hidden_after"])
        self.assertNotIn("Settings", plan["visible_after"])

    def test_a_tab_the_course_does_not_have_is_dropped_rather_than_written(self):
        plan = nav.plan(TABS, KEEP)
        self.assertEqual(plan["dropped"], ["context_external_tool_382357"])
        self.assertNotIn("context_external_tool_382357", plan["keep"])

    def test_the_sentence_names_what_is_left_showing(self):
        plan = nav.plan(TABS, KEEP)
        sentence = nav.sentence_for(plan, "Test course")
        self.assertIn("hide 3 tabs", sentence)
        self.assertIn("show 1 tab", sentence)
        self.assertIn("Home > Announcements > Modules > Grades", sentence)
        self.assertIn("No content is changed", sentence)

    def test_nothing_is_written_when_the_person_says_no(self):
        with self.assertRaises(Refused):
            nav.apply(self.app, 101, KEEP, refusing_gate)
        self.assertEqual(self.app.content.writes, [])

    def test_applying_writes_each_change_once_and_reads_the_nav_back(self):
        seen = []
        out = nav.apply(self.app, 101, KEEP, allowing_gate(seen))
        self.assertEqual(len(seen), 1, "the gate is asked exactly once, before the first write")
        written = {t[1] for t in self.app.content.writes}
        self.assertEqual(written, {"announcements", "assignments", "quizzes",
                                   "context_external_tool_99", "modules", "grades"})
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["mismatches"], 0)
        self.assertEqual(out["visible_after_live"], ["Home", "Announcements", "Modules", "Grades"])

    def test_an_unchanged_nav_writes_nothing(self):
        nav.apply(self.app, 101, KEEP, allowing_gate([]))
        self.app.content.writes.clear()
        out = nav.apply(self.app, 101, KEEP, refusing_gate)
        self.assertEqual(out["written"], 0)
        self.assertEqual(self.app.content.writes, [])


# ============================================================== quiz backup
SOURCE_QUIZ = {
    "id": 77, "title": "Unit 3 check", "quiz_type": "assignment", "published": True,
    "description": "<p>Ten minutes.</p>", "time_limit": 20, "shuffle_answers": True,
    "allowed_attempts": 2, "access_code": "", "question_count": 3, "points_possible": 9,
    "html_url": "https://x/quizzes/77",
}
SOURCE_QUESTIONS = [
    {"id": 1, "question_name": "Colour space", "question_type": "multiple_choice_question",
     "question_text": "<p>Which one?</p>", "points_possible": 3,
     "answers": [{"id": 11, "text": "sRGB", "weight": 100, "comments": "yes"},
                 {"id": 12, "text": "CMYK", "weight": 0}]},
    {"id": 2, "question_name": "Pairs", "question_type": "matching_question",
     "question_text": "<p>Match them.</p>", "points_possible": 4,
     "matching_answer_incorrect_matches": "none",
     "answers": [{"id": 21, "left": "Diffuse", "right": "Base colour"},
                 {"id": 22, "left": "Roughness", "right": "Microsurface"}]},
    {"id": 3, "question_name": "Read this", "question_type": "text_only_question",
     "question_text": "<p>No answer needed.</p>", "points_possible": 2, "answers": []},
]


class QuizBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeContent(quizzes=[dict(SOURCE_QUIZ)],
                                  questions={"77": [dict(q) for q in SOURCE_QUESTIONS]})
        self.app = FakeApp(self.client, self.tmp.name)

    def test_the_copy_is_unpublished_whatever_the_original_is(self):
        settings = quiz_backup.backup_settings(SOURCE_QUIZ)
        self.assertIs(settings["published"], False)
        self.assertEqual(settings["title"], "BACKUP Unit 3 check")
        self.assertIn("Unpublished backup", settings["description"])
        self.assertEqual(settings["time_limit"], 20)
        self.assertNotIn("access_code", settings, "an empty access code is not copied")

    def test_the_read_shape_of_an_answer_becomes_the_write_shape(self):
        written = quiz_backup.questions_to_write(SOURCE_QUESTIONS)
        choice = written[0]["answers"]
        self.assertEqual(choice[0]["answer_text"], "sRGB")
        self.assertEqual(choice[0]["answer_weight"], 100)
        self.assertEqual(choice[0]["answer_comments"], "yes")
        self.assertNotIn("text", choice[0])
        self.assertNotIn("weight", choice[0])
        self.assertNotIn("id", choice[0])

    def test_a_matching_question_gets_the_weight_canvas_does_not_return(self):
        pairs = quiz_backup.questions_to_write(SOURCE_QUESTIONS)[1]["answers"]
        self.assertEqual(pairs[0]["answer_match_left"], "Diffuse")
        self.assertEqual(pairs[0]["answer_match_right"], "Base colour")
        self.assertEqual(pairs[0]["answer_weight"], 100)

    def test_a_text_only_question_carries_no_answers(self):
        self.assertNotIn("answers", quiz_backup.questions_to_write(SOURCE_QUESTIONS)[2])

    def test_nothing_is_created_when_the_person_says_no(self):
        with self.assertRaises(Refused):
            quiz_backup.backup(self.app, 101, 77, refusing_gate)
        self.assertEqual(self.client.created_quizzes, [])

    def test_the_backup_is_created_unpublished_and_verified_through_questions(self):
        seen = []
        entry = quiz_backup.backup(self.app, 101, 77, allowing_gate(seen))
        self.assertEqual(len(seen), 1)
        self.assertIn("unpublished quiz named BACKUP Unit 3 check", seen[0]["sentence"])
        self.assertIs(self.client.created_quizzes[0]["published"], False)
        self.assertIs(entry["published"], False)
        self.assertEqual(entry["questions_copy"], 3)
        self.assertEqual(entry["questions_source"], 3)
        self.assertEqual(entry["points_copy"], entry["points_source"])
        self.assertEqual(entry["warnings"], [])
        self.assertTrue(entry["ok"])

    def test_a_quiz_with_no_questions_is_refused_before_anything_is_created(self):
        self.client.state["questions"]["77"] = []
        with self.assertRaises(Refused):
            quiz_backup.backup(self.app, 101, 77, allowing_gate([]))
        self.assertEqual(self.client.created_quizzes, [])
        self.assertTrue(quiz_backup.plan(self.app, 101, 77)["refusal"])


# ============================================================== slo validate
SLOS = {"course": {"course": "IMT 1213", "title": "Game design"},
        "leaves": [{"id": "1.a", "text": "Build a prototype."},
                   {"id": "1.b", "text": "Document the loop."},
                   {"id": "2", "text": "Playtest and revise."}]}
ITEMS = {"course_id": "101", "course_name": "Test course", "items": [
    {"id": "assignment-11", "name": "Prototype", "type": "assignment", "points": 100, "graded": True},
    {"id": "quiz-22", "name": "Loop quiz", "type": "quiz", "points": 20, "graded": True},
    {"id": "page-playtesting", "name": "Playtesting", "type": "page", "graded": False},
]}


def alignment(rows):
    return {"program": "Simulation", "framework": "x.pdf", "alignment": rows}


GOOD = alignment([
    {"slo": "1.a", "verdict": "assessed", "evidence": ["assignment-11"], "rationale": "They build one."},
    {"slo": "1.b", "verdict": "partially-assessed", "evidence": ["quiz-22"],
     "rationale": "Recognition, not writing."},
    {"slo": "2", "verdict": "ungraded-only", "evidence": ["page-playtesting"],
     "rationale": "A reading covers it.", "suggestion": "Make the playtest report graded."},
])


class SloValidateTest(unittest.TestCase):
    def test_a_complete_honest_mapping_passes(self):
        out = slo.validate(SLOS, ITEMS, GOOD)
        self.assertTrue(out["ok"], out["errors"])
        self.assertEqual(out["counts"]["outcomes"], 3)
        self.assertEqual(out["counts"]["assessed"], 1)
        self.assertEqual(out["counts"]["gaps"], 1)
        self.assertEqual(out["counts"]["coverage"], 50.0)

    def test_an_invented_outcome_id_is_rejected(self):
        rows = [dict(r) for r in GOOD["alignment"]]
        rows.append({"slo": "3.c", "verdict": "assessed", "evidence": ["assignment-11"],
                     "rationale": "Made up."})
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertFalse(out["ok"])
        self.assertEqual(out["invented"], ["3.c"])
        self.assertTrue(any("not in the framework" in e for e in out["errors"]))

    def test_an_omitted_outcome_is_rejected(self):
        out = slo.validate(SLOS, ITEMS, alignment(GOOD["alignment"][:2]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["missing"], ["2"])
        self.assertTrue(any("left out" in e for e in out["errors"]))

    def test_the_same_outcome_twice_is_rejected(self):
        rows = list(GOOD["alignment"]) + [dict(GOOD["alignment"][0])]
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertFalse(out["ok"])
        self.assertEqual(out["duplicates"], ["1.a"])

    def test_a_verdict_outside_the_fixed_set_is_rejected(self):
        rows = [dict(r) for r in GOOD["alignment"]]
        rows[0]["verdict"] = "mostly fine"
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertFalse(out["ok"])
        self.assertTrue(any("is not one of" in e for e in out["errors"]))

    def test_evidence_that_is_not_in_the_course_is_rejected(self):
        rows = [dict(r) for r in GOOD["alignment"]]
        rows[0]["evidence"] = ["assignment-99"]
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertFalse(out["ok"])
        self.assertTrue(any("not in this course" in e for e in out["errors"]))

    def test_coverage_claimed_with_nothing_cited_is_rejected(self):
        rows = [dict(r) for r in GOOD["alignment"]]
        rows[0]["evidence"] = []
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertFalse(out["ok"])
        self.assertTrue(any("nothing cited as evidence" in e for e in out["errors"]))

    def test_a_framework_with_no_outcomes_is_never_reported_as_aligned(self):
        out = slo.validate({"leaves": []}, ITEMS, alignment([]))
        self.assertFalse(out["ok"])
        self.assertTrue(any("no outcomes" in e for e in out["errors"]))

    def test_a_missing_reason_is_a_warning_not_a_refusal(self):
        rows = [dict(r) for r in GOOD["alignment"]]
        rows[0]["rationale"] = ""
        out = slo.validate(SLOS, ITEMS, alignment(rows))
        self.assertTrue(out["ok"])
        self.assertTrue(any("no reason given" in w for w in out["warnings"]))

    def test_the_outcome_leaves_come_out_of_a_nested_framework_course(self):
        nested = {"course": {"outcomes": [
            {"n": "1", "text": "Model", "sub": [
                {"n": "a", "text": "Box model", "sub": []},
                {"n": "b", "text": "Retopologise", "sub": []}]},
            {"n": "2", "text": "Texture", "sub": []}]}}
        self.assertEqual([leaf["id"] for leaf in slo.leaf_outcomes(nested)], ["1.a", "1.b", "2"])

    def test_a_course_code_splits_into_a_prefix_and_a_number(self):
        self.assertEqual(slo.split_code("ATT 1214 001"), ("ATT", "1214"))
        self.assertEqual(slo.split_code("IMT1213"), ("IMT", "1213"))
        self.assertEqual(slo.split_code(""), (None, None))

    def test_resolve_turns_a_shared_prefix_into_choices_rather_than_a_guess(self):
        programs = [
            {"name": "Simulation and Game Design", "cip": "50.0411", "prefixes": ["IMT"],
             "slug": "/curriculum/sim", "url": "https://x/sim"},
            {"name": "Modeling and Virtual Environments", "cip": "11.0804", "prefixes": ["IMT"],
             "slug": "/curriculum/mve", "url": "https://x/mve"},
            {"name": "Welding", "cip": "48.0508", "prefixes": ["WLT"],
             "slug": "/curriculum/weld", "url": "https://x/weld"},
        ]
        many = slo.resolve("IMT 1213", programs=programs)
        self.assertTrue(many["ambiguous"])
        self.assertIsNone(many["resolved"])
        self.assertEqual(len(many["choices"]), 2)

        one = slo.resolve("WLT 1119", programs=programs)
        self.assertFalse(one["ambiguous"])
        self.assertEqual(one["resolved"]["name"], "Welding")

        none = slo.resolve("ENG 1113", programs=programs)
        self.assertEqual(none["candidates"], [])
        self.assertIn("articulation agreement", none["conclusion"])


class PageClient(FakeContent):
    """A fake that keeps pages, so the report push can be read back."""

    def __init__(self):
        super().__init__()
        self.pages_store = {}

    def page(self, course_id, slug):
        if slug not in self.pages_store:
            raise RuntimeError("404 page not found")
        return dict(self.pages_store[slug])

    def update_page(self, course_id, slug, body=None, title=None, published=None):
        rec = self.pages_store.setdefault(slug, {"url": slug})
        if body is not None:
            rec["body"] = body
        if title is not None:
            rec["title"] = title
        if published is not None:
            rec["published"] = bool(published)
        rec["html_url"] = f"https://x/courses/{course_id}/pages/{slug}"
        self.writes.append(("page", slug, {"published": published, "chars": len(body or "")}))
        return dict(rec)


class SloReportTest(unittest.TestCase):
    def setUp(self):
        from courseforge.courseops.common import save_json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = FakeApp(PageClient(), self.tmp.name)
        folder = slo.slo_dir(self.app, 101)
        save_json(folder / slo.SLOS_FILE, SLOS)
        save_json(folder / slo.ITEMS_FILE, ITEMS)
        save_json(folder / slo.ALIGNMENT_FILE, GOOD)
        save_json(folder / "fetch.json",
                  {"program": {"name": "Simulation"}, "framework": {"name": "sim.pdf", "year": "2025"}})

    def test_the_dry_run_renders_both_files_and_sends_nothing(self):
        plan = slo.report(self.app, 101, apply=False)
        self.assertFalse(plan["applied"])
        self.assertTrue(Path(plan["md_path"]).is_file())
        self.assertTrue(Path(plan["html_path"]).is_file())
        self.assertIn("unpublished", plan["sentence"])
        self.assertEqual(plan["counts"]["coverage"], 50.0)
        self.assertEqual(self.app.content.writes, [])

    def test_the_page_is_pushed_unpublished_to_the_one_fixed_slug(self):
        seen = []
        out = slo.report(self.app, 101, apply=True, gate=allowing_gate(seen))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["kind"], "courseops.slo_report")
        kind, slug, fields = self.app.content.writes[0]
        self.assertEqual(slug, slo.REPORT_SLUG)
        self.assertIs(fields["published"], False)
        self.assertIs(out["published"], False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["warnings"], [])

    def test_running_it_again_replaces_the_same_page(self):
        slo.report(self.app, 101, apply=True, gate=allowing_gate([]))
        seen = []
        slo.report(self.app, 101, apply=True, gate=allowing_gate(seen))
        self.assertEqual(list(self.app.content.pages_store), [slo.REPORT_SLUG])
        self.assertIn("Replace the body", seen[0]["sentence"])

    def test_nothing_is_written_when_the_person_says_no(self):
        with self.assertRaises(Refused):
            slo.report(self.app, 101, apply=True, gate=refusing_gate)
        self.assertEqual(self.app.content.writes, [])

    def test_an_alignment_that_fails_the_check_writes_no_report(self):
        from courseforge.courseops.common import save_json
        save_json(slo.slo_dir(self.app, 101) / slo.ALIGNMENT_FILE, alignment(
            [{"slo": "9.z", "verdict": "assessed", "evidence": ["assignment-11"], "rationale": "x"}]))
        with self.assertRaises(Refused):
            slo.report(self.app, 101, apply=False)
        self.assertEqual(self.app.content.writes, [])

    def test_the_prompt_carries_every_outcome_and_every_item_id(self):
        prompt = slo.build_prompt(SLOS, ITEMS)
        for leaf in SLOS["leaves"]:
            self.assertIn(leaf["id"], prompt)
        for item in ITEMS["items"]:
            self.assertIn(item["id"], prompt)
        for verdict in slo.VERDICTS:
            self.assertIn(verdict, prompt)

    def test_the_local_state_walks_the_four_steps(self):
        self.assertEqual(slo.state(self.app, 101)["step"], "report")
        slo.report(self.app, 101, apply=True, gate=allowing_gate([]))
        st = slo.state(self.app, 101)
        self.assertEqual(st["step"], "done")
        self.assertEqual(st["report_slug"], slo.REPORT_SLUG)
        self.assertEqual(len(st["alignment"]), 3)
        self.assertEqual(st["alignment"][0]["evidence"][0]["name"], "Prototype")


if __name__ == "__main__":
    unittest.main()
