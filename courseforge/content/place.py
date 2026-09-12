"""Put one draft into the course: create the page, assignment, discussion or
quiz and its module item, then read it back.

Defaults to unpublished. A quiz is created unpublished, its questions posted
in the WRITE shape, and published only if asked. A syllabus draft replaces the
course's syllabus body (backed up locally first) and has no module item.
Module items go as JSON. Nothing here calls the confirm gate or the ledger;
the route does that around this call so the CLI and the tests can use it too.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from . import check_quiz, generate
from .common import course_url, num, publish_word, quiet_log, same_text


def sentence(draft: dict, module_name: str | None, position, publish: bool, course_name: str) -> str:
    kind = draft.get("kind") or "page"
    title = draft.get("title") or "(untitled)"
    pub = publish_word(publish)
    where = ""
    if module_name:
        where = f" in module '{module_name}'"
        if position not in (None, ""):
            where += f" at position {position}"
    if kind == "syllabus":
        return (f"Replace the syllabus of {course_name} with the draft '{title}'. The current syllabus "
                "text is saved locally first, so this can be undone.")
    if kind == "quiz":
        n = len((draft.get("quiz") or {}).get("questions") or [])
        pts = num((draft.get("assignment") or {}).get("points"))
        return (f"Create the {pub} quiz '{title}' with {n} questions worth {pts:g} points{where} in "
                f"{course_name}. Existing content is not touched.")
    if kind in ("assignment", "discussion"):
        pts = num((draft.get("assignment") or {}).get("points"))
        what = "assignment" if kind == "assignment" else ("graded discussion" if pts else "discussion")
        return (f"Create the {pub} {what} '{title}' worth {pts:g} points{where} in {course_name}. "
                "Existing content is not touched.")
    return f"Create the {pub} page '{title}'{where} in {course_name}. Existing content is not touched."


def place(client, base_url: str, course_id, draft: dict, module_id=None, position=None,
          publish: bool = False, group: str | None = None, log: Callable | None = None,
          backup_dir: Path | None = None) -> dict:
    log = quiet_log(log)
    failures = generate.hard_failures(draft)
    if failures:
        raise ValueError("The draft still fails the style check and was not placed: " +
                         "; ".join(failures[:3]))
    kind = draft.get("kind") or "page"
    title = (draft.get("title") or "").strip()
    html = draft.get("html") or ""
    if not title or not html.strip():
        raise ValueError("The draft needs a title and a body before it can be placed.")
    out: dict = {"kind": kind, "title": title, "published": bool(publish), "module_id": module_id,
                 "item_id": None, "id": None, "url": None, "html_url": None, "mismatches": []}

    def group_id(name):
        if not name:
            return None
        return client.ensure_assignment_group(course_id, name).get("id")

    item: dict | None = None
    if kind in ("page", "study-guide"):
        log(f"creating the {publish_word(publish)} page '{title}'")
        r = client.create_page(course_id, title, html, published=publish)
        out["id"], out["url"] = r.get("page_id"), r.get("url")
        out["html_url"] = r.get("html_url") or course_url(base_url, course_id, f"/pages/{r.get('url')}")
        back = client.page(course_id, r.get("url"))
        if not same_text(back.get("body") or "", html):
            out["mismatches"].append("the page body read back differs from the draft")
        if bool(back.get("published")) != bool(publish):
            out["mismatches"].append(f"the page read back as {publish_word(bool(back.get('published')))}")
        item = {"type": "Page", "page_url": r.get("url"), "title": title}

    elif kind == "syllabus":
        log("backing up the current syllabus, then replacing it")
        old = client.course_detail(course_id, include=["syllabus_body"]).get("syllabus_body") or ""
        if backup_dir is not None:
            Path(backup_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = Path(backup_dir) / f"syllabus-{stamp}.html"
            path.write_text(old, encoding="utf-8")
            out["backup"] = str(path)
        client.update_course_content(course_id, syllabus_body=html)
        back = client.course_detail(course_id, include=["syllabus_body"]).get("syllabus_body") or ""
        if not same_text(back, html):
            out["mismatches"].append("the syllabus read back differs from the draft")
        out["id"] = str(course_id)
        out["html_url"] = course_url(base_url, course_id, "/assignments/syllabus")

    elif kind == "assignment":
        a = draft.get("assignment") or {}
        fields = {"name": title, "description": html, "points_possible": num(a.get("points")),
                  "published": bool(publish), "grading_type": "points"}
        subs = a.get("submission_types") or ["online_upload"]
        fields["submission_types"] = list(subs)
        gid = group_id(group or draft.get("group"))
        if gid:
            fields["assignment_group_id"] = gid
        log(f"creating the {publish_word(publish)} assignment '{title}' ({fields['points_possible']:g} points)")
        r = client.create_assignment(course_id, **fields)
        out["id"], out["html_url"] = r.get("id"), r.get("html_url")
        if num(r.get("points_possible"), -1) != fields["points_possible"]:
            out["mismatches"].append(f"points read back as {r.get('points_possible')}")
        if not same_text(r.get("description") or "", html):
            out["mismatches"].append("the description read back differs from the draft")
        item = {"type": "Assignment", "content_id": int(r["id"])}

    elif kind == "discussion":
        a = draft.get("assignment") or {}
        pts = num(a.get("points"))
        assignment = None
        if pts > 0:
            assignment = {"points_possible": pts, "grading_type": "points",
                          "submission_types": ["discussion_topic"]}
            gid = group_id(group or draft.get("group"))
            if gid:
                assignment["assignment_group_id"] = gid
        log(f"creating the {publish_word(publish)} discussion '{title}'")
        r = client.create_discussion(course_id, title, html, published=publish, assignment=assignment)
        out["id"], out["html_url"] = r.get("id"), r.get("html_url")
        if assignment and not (r.get("assignment") or {}).get("id"):
            out["mismatches"].append("Canvas returned no grading assignment for the discussion")
        if not same_text(r.get("message") or "", html):
            out["mismatches"].append("the message read back differs from the draft")
        item = {"type": "Discussion", "content_id": int(r["id"])}

    elif kind == "quiz":
        quiz = draft.get("quiz") or {}
        questions = list(quiz.get("questions") or [])
        rep = check_quiz.check(questions)
        if not rep["ok"]:
            raise ValueError("The quiz questions still have problems: " + "; ".join(rep["failed"][:3]))
        meta = {"title": title, "quiz_type": "assignment", "published": False}
        if quiz.get("time_limit"):
            meta["time_limit"] = int(quiz["time_limit"])
        if quiz.get("shuffle_answers"):
            meta["shuffle_answers"] = True
        gid = group_id(group or draft.get("group"))
        if gid:
            meta["assignment_group_id"] = gid
        log(f"creating the quiz '{title}' unpublished, then {len(questions)} questions")
        r = client.create_quiz(course_id, **meta)
        qid = r["id"]
        client.update_quiz_content(course_id, qid, description=html)
        for n, q in enumerate(questions, 1):
            client.create_quiz_question(course_id, qid, check_quiz.to_write_shape(q, n))
        back = list(client.quiz_questions(course_id, qid))
        if len(back) != len(questions):
            out["mismatches"].append(f"{len(back)} questions read back, {len(questions)} sent")
        if publish:
            log("publishing the quiz")
            client.update_quiz_content(course_id, qid, published=True)
        out["id"], out["html_url"] = qid, r.get("html_url")
        out["questions"] = len(questions)
        item = {"type": "Quiz", "content_id": int(qid)}
    else:
        raise ValueError(f"unknown draft kind {kind!r}")

    if item is not None and module_id:
        if position not in (None, ""):
            item["position"] = int(position)
        item["published"] = bool(publish)
        log("adding the module item")
        added = client.add_module_item(course_id, int(module_id), item)
        out["item_id"] = added.get("id")
        if not added.get("id"):
            out["mismatches"].append("Canvas returned no module item id")
    out["placed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return out
