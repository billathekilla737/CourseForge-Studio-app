"""Course content operations for CanvasClient: pages, assignments as content,
discussions, quizzes and their questions, the syllabus, modules, tabs,
assignment groups and rubrics.

Mixed into CanvasClient (see canvas.py). Everything here uses the transport the
grader already had (`self._request`, `self.paged`) plus two encoders below.

Body encoding is per endpoint and not obvious. Learned the hard way, and kept:
- quiz descriptions, tabs, module items, quiz questions -> JSON
- pages, assignments, discussions, course settings, module create/update -> form
Quiz PUTs take one concern at a time: a combined description+dates+title PUT can
400 while each field alone succeeds. `PUT /pages/:slug` upserts, so always reuse
the slug Canvas returned; editing a title regenerates it.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Iterator

# Assignment fields content work may set. Dates are the grading area's business
# (canvas.DATE_FIELDS); grades never appear here.
ASSIGNMENT_CONTENT_FIELDS = (
    "name", "description", "points_possible", "grading_type", "submission_types",
    "allowed_extensions", "assignment_group_id", "position", "published",
    "due_at", "unlock_at", "lock_at", "peer_reviews", "omit_from_final_grade",
    "notify_of_update", "only_visible_to_overrides",
)
QUIZ_CONTENT_FIELDS = (
    "title", "description", "quiz_type", "assignment_group_id", "time_limit",
    "shuffle_answers", "hide_results", "show_correct_answers", "allowed_attempts",
    "scoring_policy", "one_question_at_a_time", "cant_go_back", "access_code",
    "ip_filter", "due_at", "lock_at", "unlock_at", "published", "one_time_results",
    "require_lockdown_browser", "show_correct_answers_at", "hide_correct_answers_at",
)
COURSE_CONTENT_FIELDS = ("syllabus_body", "default_view", "name", "course_code",
                         "is_public_to_auth_users", "public_syllabus", "grading_standard_id")


class ContentOps:
    # ------------------------------------------------------------ encoders
    def _post_form(self, method: str, path: str, fields: list[tuple[str, Any]]) -> Any:
        """application/x-www-form-urlencoded with Canvas's bracket keys."""
        body = urllib.parse.urlencode([(k, "" if v is None else str(v)) for k, v in fields],
                                      quote_via=urllib.parse.quote).encode("utf-8")
        payload, _ = self._request(method, path, body, "application/x-www-form-urlencoded")
        return payload

    def _send_json(self, method: str, path: str, obj: Any) -> Any:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        payload, _ = self._request(method, path, body, "application/json")
        return payload

    @staticmethod
    def _fields(prefix: str, obj: dict) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for key, value in obj.items():
            if isinstance(value, (list, tuple)):
                for v in value:
                    out.append((f"{prefix}[{key}][]", v))
            elif isinstance(value, bool):
                out.append((f"{prefix}[{key}]", "true" if value else "false"))
            elif value is not None:
                out.append((f"{prefix}[{key}]", value))
        return out

    # --------------------------------------------------------------- pages
    def pages(self, course_id) -> list[dict]:
        """Page list (no bodies; the list endpoint never carries them)."""
        return list(self.paged(f"/courses/{course_id}/pages", sort="title"))

    def page(self, course_id, slug: str) -> dict:
        return self.get(f"/courses/{course_id}/pages/{urllib.parse.quote(str(slug), safe='')}")

    def front_page(self, course_id) -> dict | None:
        try:
            return self.get(f"/courses/{course_id}/front_page")
        except Exception:  # noqa: BLE001
            return None

    def create_page(self, course_id, title: str, body: str, published: bool = False,
                    front_page: bool = False, editing_roles: str | None = None) -> dict:
        fields = [("wiki_page[title]", title), ("wiki_page[body]", body),
                  ("wiki_page[published]", "true" if published else "false")]
        if front_page:
            fields.append(("wiki_page[front_page]", "true"))
        if editing_roles:
            fields.append(("wiki_page[editing_roles]", editing_roles))
        return self._post_form("POST", f"/courses/{course_id}/pages", fields)

    def update_page(self, course_id, slug: str, body: str | None = None,
                    title: str | None = None, published: bool | None = None,
                    front_page: bool | None = None) -> dict:
        """Upsert by slug. Trust the `url` in the response afterwards."""
        fields: list[tuple[str, Any]] = []
        if body is not None:
            fields.append(("wiki_page[body]", body))
        if title is not None:
            fields.append(("wiki_page[title]", title))
        if published is not None:
            fields.append(("wiki_page[published]", "true" if published else "false"))
        if front_page is not None:
            fields.append(("wiki_page[front_page]", "true" if front_page else "false"))
        return self._post_form("PUT", f"/courses/{course_id}/pages/"
                               f"{urllib.parse.quote(str(slug), safe='')}", fields)

    def delete_page(self, course_id, slug: str) -> Any:
        payload, _ = self._request("DELETE", f"/courses/{course_id}/pages/"
                                   f"{urllib.parse.quote(str(slug), safe='')}")
        return payload

    # --------------------------------------------------- assignments (content)
    def assignments_content(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/assignments",
                               **{"include": ["assignment_visibility"], "order_by": "position"}))

    def create_assignment(self, course_id, **fields) -> dict:
        clean = {k: v for k, v in fields.items() if k in ASSIGNMENT_CONTENT_FIELDS}
        return self._post_form("POST", f"/courses/{course_id}/assignments",
                               self._fields("assignment", clean))

    def update_assignment_content(self, course_id, assignment_id, **fields) -> dict:
        """Name, description, points, group, submission types. Refuses anything
        outside ASSIGNMENT_CONTENT_FIELDS rather than passing it through."""
        bad = [k for k in fields if k not in ASSIGNMENT_CONTENT_FIELDS]
        if bad:
            raise ValueError(f"assignment fields not allowed here: {', '.join(bad)}")
        return self._post_form("PUT", f"/courses/{course_id}/assignments/{assignment_id}",
                               self._fields("assignment", fields))

    # ----------------------------------------------------------- discussions
    def discussions(self, course_id, announcements: bool = False) -> list[dict]:
        params = {"only_announcements": "true"} if announcements else {}
        return list(self.paged(f"/courses/{course_id}/discussion_topics", **params))

    def discussion(self, course_id, topic_id) -> dict:
        return self.get(f"/courses/{course_id}/discussion_topics/{topic_id}")

    def create_discussion(self, course_id, title: str, message: str, published: bool = False,
                          assignment: dict | None = None, is_announcement: bool = False,
                          delayed_post_at: str | None = None, **extra) -> dict:
        fields = [("title", title), ("message", message),
                  ("published", "true" if published else "false")]
        if is_announcement:
            fields.append(("is_announcement", "true"))
        if delayed_post_at:
            fields.append(("delayed_post_at", delayed_post_at))
        for key, value in extra.items():
            fields.append((key, value))
        if assignment:
            fields.extend(self._fields("assignment", assignment))
        return self._post_form("POST", f"/courses/{course_id}/discussion_topics", fields)

    def update_discussion(self, course_id, topic_id, **fields) -> dict:
        flat: list[tuple[str, Any]] = []
        for key, value in fields.items():
            if key == "assignment" and isinstance(value, dict):
                flat.extend(self._fields("assignment", value))
            elif isinstance(value, bool):
                flat.append((key, "true" if value else "false"))
            elif value is not None:
                flat.append((key, value))
        return self._post_form("PUT", f"/courses/{course_id}/discussion_topics/{topic_id}", flat)

    # --------------------------------------------------------------- quizzes
    def quizzes_content(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/quizzes"))

    def quiz_content(self, course_id, quiz_id) -> dict:
        return self.get(f"/courses/{course_id}/quizzes/{quiz_id}")

    def create_quiz(self, course_id, **fields) -> dict:
        clean = {k: v for k, v in fields.items() if k in QUIZ_CONTENT_FIELDS}
        return self._send_json("POST", f"/courses/{course_id}/quizzes", {"quiz": clean})

    def update_quiz_content(self, course_id, quiz_id, **fields) -> dict:
        """JSON, one concern per call. A description write and a dates write
        should be two PUTs; combined they can 400."""
        bad = [k for k in fields if k not in QUIZ_CONTENT_FIELDS]
        if bad:
            raise ValueError(f"quiz fields not allowed here: {', '.join(bad)}")
        return self._send_json("PUT", f"/courses/{course_id}/quizzes/{quiz_id}", {"quiz": fields})

    def quiz_questions(self, course_id, quiz_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/quizzes/{quiz_id}/questions"))

    def create_quiz_question(self, course_id, quiz_id, question: dict) -> dict:
        """WRITE shape: answers carry answer_text / answer_weight (not text / weight)."""
        return self._send_json("POST", f"/courses/{course_id}/quizzes/{quiz_id}/questions",
                               {"question": question})

    def update_quiz_question(self, course_id, quiz_id, question_id, question: dict) -> dict:
        return self._send_json("PUT", f"/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}",
                               {"question": question})

    def delete_quiz_question(self, course_id, quiz_id, question_id) -> Any:
        payload, _ = self._request("DELETE", f"/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}")
        return payload

    @staticmethod
    def question_read_to_write(q: dict) -> dict:
        """Map a question as Canvas RETURNS it to the shape Canvas ACCEPTS."""
        out = {k: q.get(k) for k in ("question_name", "question_text", "question_type",
                                     "points_possible", "correct_comments", "incorrect_comments",
                                     "neutral_comments", "matching_answer_incorrect_matches")
               if q.get(k) is not None}
        answers = []
        for a in q.get("answers") or []:
            w = {}
            if a.get("html") or a.get("text"):
                w["answer_text"] = a.get("text") or ""
                if a.get("html"):
                    w["answer_html"] = a["html"]
            if a.get("weight") is not None:
                w["answer_weight"] = a["weight"]
            if a.get("left") is not None:
                w["answer_match_left"] = a["left"]
            if a.get("right") is not None:
                w["answer_match_right"] = a["right"]
            if a.get("comments"):
                w["answer_comments"] = a["comments"]
            if a.get("blank_id"):
                w["blank_id"] = a["blank_id"]
            for k in ("numerical_answer_type", "exact", "margin", "start", "end", "approximate", "precision"):
                if a.get(k) is not None:
                    w[k] = a[k]
            answers.append(w)
        if answers:
            out["answers"] = answers
        return out

    # ------------------------------------------------------ course as content
    def course_detail(self, course_id, include: list[str] | None = None) -> dict:
        return self.get(f"/courses/{course_id}", **({"include": include} if include else {}))

    def update_course_content(self, course_id, **fields) -> dict:
        bad = [k for k in fields if k not in COURSE_CONTENT_FIELDS]
        if bad:
            raise ValueError(f"course fields not allowed here: {', '.join(bad)}")
        return self._post_form("PUT", f"/courses/{course_id}", self._fields("course", fields))

    # --------------------------------------------------------------- modules
    def modules(self, course_id, include_items: bool = False) -> list[dict]:
        params = {"include": ["items"]} if include_items else {}
        return list(self.paged(f"/courses/{course_id}/modules", **params))

    def module_items(self, course_id, module_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/modules/{module_id}/items"))

    def create_module(self, course_id, name: str, position: int | None = None,
                      published: bool | None = None) -> dict:
        fields = [("module[name]", name)]
        if position is not None:
            fields.append(("module[position]", position))
        payload = self._post_form("POST", f"/courses/{course_id}/modules", fields)
        if published is not None and payload.get("id"):
            payload = self.update_module(course_id, payload["id"], published=published)
        return payload

    def update_module(self, course_id, module_id, **fields) -> dict:
        return self._post_form("PUT", f"/courses/{course_id}/modules/{module_id}",
                               self._fields("module", fields))

    def delete_module(self, course_id, module_id) -> Any:
        """Deleting a module orphans content only it referenced. Callers decide."""
        payload, _ = self._request("DELETE", f"/courses/{course_id}/modules/{module_id}")
        return payload

    def add_module_item(self, course_id, module_id, item: dict) -> dict:
        """JSON body required. item: {type, content_id | page_url | external_url,
        title, position, indent, published, new_tab}."""
        return self._send_json("POST", f"/courses/{course_id}/modules/{module_id}/items",
                               {"module_item": item})

    def update_module_item(self, course_id, module_id, item_id, item: dict) -> dict:
        """Titles, position, indent, published. NOT content_id: Canvas answers 200
        and keeps serving the old content. Delete and recreate to retarget."""
        if "content_id" in item:
            raise ValueError("module items cannot be retargeted; delete and recreate")
        return self._send_json("PUT", f"/courses/{course_id}/modules/{module_id}/items/{item_id}",
                               {"module_item": item})

    def delete_module_item(self, course_id, module_id, item_id) -> Any:
        payload, _ = self._request("DELETE", f"/courses/{course_id}/modules/{module_id}/items/{item_id}")
        return payload

    # ------------------------------------------------------------------ tabs
    def tabs(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/tabs"))

    def update_tab(self, course_id, tab_id: str, hidden: bool | None = None,
                   position: int | None = None) -> dict:
        """JSON only. A form body is accepted with 200 and silently ignored."""
        body: dict = {}
        if hidden is not None:
            body["hidden"] = bool(hidden)
        if position is not None:
            body["position"] = int(position)
        return self._send_json("PUT", f"/courses/{course_id}/tabs/{tab_id}", body)

    # ---------------------------------------------------- assignment groups
    def assignment_groups(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/assignment_groups"))

    def create_assignment_group(self, course_id, name: str, weight: float | None = None) -> dict:
        fields = [("name", name)]
        if weight is not None:
            fields.append(("group_weight", weight))
        return self._post_form("POST", f"/courses/{course_id}/assignment_groups", fields)

    def ensure_assignment_group(self, course_id, name: str) -> dict:
        for g in self.assignment_groups(course_id):
            if (g.get("name") or "").strip().lower() == name.strip().lower():
                return g
        return self.create_assignment_group(course_id, name)

    # ---------------------------------------------------------------- rubrics
    def rubrics(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/rubrics"))

    def rubric(self, course_id, rubric_id, include: list[str] | None = None) -> dict:
        return self.get(f"/courses/{course_id}/rubrics/{rubric_id}",
                        **({"include": include} if include else {}))

    def _rubric_fields(self, title: str, criteria: list[dict], association: dict | None,
                       free_form: bool = False) -> list[tuple[str, Any]]:
        fields: list[tuple[str, Any]] = [
            ("rubric[title]", title),
            ("rubric[free_form_criterion_comments]", "true" if free_form else "false"),
        ]
        for i, crit in enumerate(criteria):
            fields.append((f"rubric[criteria][{i}][description]", crit.get("description", "")))
            fields.append((f"rubric[criteria][{i}][long_description]", crit.get("long_description", "")))
            fields.append((f"rubric[criteria][{i}][points]", crit.get("points", 0)))
            if crit.get("criterion_use_range"):
                fields.append((f"rubric[criteria][{i}][criterion_use_range]", "true"))
            for j, rating in enumerate(crit.get("ratings") or []):
                fields.append((f"rubric[criteria][{i}][ratings][{j}][description]", rating.get("description", "")))
                fields.append((f"rubric[criteria][{i}][ratings][{j}][long_description]", rating.get("long_description", "")))
                fields.append((f"rubric[criteria][{i}][ratings][{j}][points]", rating.get("points", 0)))
        if association:
            fields.append(("rubric_association[association_type]", association.get("type", "Assignment")))
            fields.append(("rubric_association[association_id]", association["id"]))
            fields.append(("rubric_association[use_for_grading]",
                           "true" if association.get("use_for_grading", True) else "false"))
            fields.append(("rubric_association[purpose]", association.get("purpose", "grading")))
            if association.get("hide_score_total") is not None:
                fields.append(("rubric_association[hide_score_total]",
                               "true" if association["hide_score_total"] else "false"))
        return fields

    def create_rubric(self, course_id, title: str, criteria: list[dict],
                      association: dict | None = None, free_form: bool = False) -> dict:
        return self._post_form("POST", f"/courses/{course_id}/rubrics",
                               self._rubric_fields(title, criteria, association, free_form))

    def update_rubric(self, course_id, rubric_id, title: str, criteria: list[dict],
                      association: dict | None = None, free_form: bool = False) -> dict:
        return self._post_form("PUT", f"/courses/{course_id}/rubrics/{rubric_id}",
                               self._rubric_fields(title, criteria, association, free_form))

    # ------------------------------------------------------------- iterators
    def iter_content_bodies(self, course_id) -> Iterator[dict]:
        """Every HTML body an accessibility pass touches, as
        {kind, id, key, title, body, url, published}. Pages need one GET each;
        quiz/discussion-backed assignment shells are skipped (their body lives
        on the quiz or topic and a write to the shell 400s)."""
        for p in self.pages(course_id):
            slug = p.get("url")
            if not slug:
                continue
            full = self.page(course_id, slug)
            yield {"kind": "page", "id": slug, "key": f"page_{slug}", "title": full.get("title"),
                   "body": full.get("body") or "", "url": full.get("html_url"),
                   "published": full.get("published"), "front_page": full.get("front_page")}
        for a in self.assignments_content(course_id):
            if a.get("quiz_id") or a.get("discussion_topic"):
                continue
            yield {"kind": "assignment", "id": a["id"], "key": f"assignment_{a['id']}",
                   "title": a.get("name"), "body": a.get("description") or "",
                   "url": a.get("html_url"), "published": a.get("published")}
        for d in self.discussions(course_id):
            yield {"kind": "discussion", "id": d["id"], "key": f"discussion_{d['id']}",
                   "title": d.get("title"), "body": d.get("message") or "",
                   "url": d.get("html_url"), "published": d.get("published")}
        for q in self.quizzes_content(course_id):
            yield {"kind": "quiz", "id": q["id"], "key": f"quiz_{q['id']}",
                   "title": q.get("title"), "body": q.get("description") or "",
                   "url": q.get("html_url"), "published": q.get("published")}
        course = self.course_detail(course_id, include=["syllabus_body"])
        yield {"kind": "syllabus", "id": str(course_id), "key": "syllabus",
               "title": "Syllabus", "body": course.get("syllabus_body") or "",
               "url": f"{self.base}/courses/{course_id}/assignments/syllabus",
               "published": True}

    def write_content_body(self, course_id, kind: str, ident, body: str) -> dict:
        """Write one HTML body back, with the right encoding for its kind."""
        if kind == "page":
            return self.update_page(course_id, ident, body=body)
        if kind == "assignment":
            return self.update_assignment_content(course_id, ident, description=body)
        if kind == "discussion":
            return self.update_discussion(course_id, ident, message=body)
        if kind == "quiz":
            return self.update_quiz_content(course_id, ident, description=body)
        if kind == "syllabus":
            return self.update_course_content(course_id, syllabus_body=body)
        raise ValueError(f"unknown content kind {kind!r}")

    def read_content_body(self, course_id, kind: str, ident) -> str:
        if kind == "page":
            return self.page(course_id, ident).get("body") or ""
        if kind == "assignment":
            return self.get(f"/courses/{course_id}/assignments/{ident}").get("description") or ""
        if kind == "discussion":
            return self.discussion(course_id, ident).get("message") or ""
        if kind == "quiz":
            return self.quiz_content(course_id, ident).get("description") or ""
        if kind == "syllabus":
            return self.course_detail(course_id, include=["syllabus_body"]).get("syllabus_body") or ""
        raise ValueError(f"unknown content kind {kind!r}")
