"""Minimal Canvas LMS REST client. Standard library only.

Read paths cover everything the grader needs: courses, assignments (with
rubrics), submissions, discussion topics and their entries, and file downloads.
The write paths (post_grade, the post-policy setters, release_grades) are all
guarded by Config.allow_canvas_writes at the call site.

Grade *posting* -- whether a student can see a grade that is in the gradebook --
has no REST surface in Canvas. It lives entirely in GraphQL, so this client
speaks both: REST for everything else, GraphQL for post policies and for the
Post / Hide buttons.
"""
from __future__ import annotations

import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import canvas_policy
from .canvas_content import ContentOps
from .canvas_course import CourseOps
from .canvas_files import FilesOps

USER_AGENT = "courseforge-studio/2.0 (+local instructor tool)"
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class CanvasError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = url
        self.body = body
        hint = ""
        if status == 401:
            hint = "  (token rejected -- expired, revoked, or wrong Canvas host)"
        elif status == 403:
            hint = "  (token lacks permission for this course or endpoint)"
        elif status == 404:
            hint = "  (no such course/assignment, or you are not enrolled in it)"
        super().__init__(f"Canvas HTTP {status} on {url}{hint}\n{body[:400]}")


class CanvasClient(ContentOps, FilesOps, CourseOps):
    """REST + GraphQL client. `scope` decides which endpoints this instance may
    touch (see canvas_policy): the grading area opens one with scope="grading";
    every other area gets scope="content", which cannot reach student data."""

    def __init__(self, base_url: str, token: str, timeout: int = 60,
                 scope: str = "full", allowed_hosts: list[str] | None = None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        if scope not in canvas_policy.SCOPES:
            raise ValueError(f"unknown Canvas client scope {scope!r}")
        self.scope = scope
        self.allowed_hosts = list(allowed_hosts or [])

    def scoped(self, scope: str) -> "CanvasClient":
        """The same connection with a different endpoint policy."""
        return CanvasClient(self.base, self.token, self.timeout, scope, self.allowed_hosts)

    # ------------------------------------------------------------ transport
    def _request(self, method: str, url: str, data: bytes | None = None,
                 content_type: str | None = None) -> tuple[Any, dict]:
        if url.startswith("/"):
            url = f"{self.base}/api/v1{url}"
        # Two rules, checked on every request: the token stays on the Canvas
        # host, and a content-scoped client never reaches student data.
        canvas_policy.assert_token_host(self.base, url, self.allowed_hosts)
        canvas_policy.check_scope(self.scope, method, url)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if content_type:
            req.add_header("Content-Type", content_type)

        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                    headers = dict(resp.headers)
                    payload = json.loads(raw) if raw.strip() else None
                    return payload, headers
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                # Canvas throttles with 403 + a Rate Limit body, and 5xx is transient.
                transient = exc.code >= 500 or (exc.code == 403 and "Rate Limit" in body)
                if not transient or attempt == 4:
                    raise CanvasError(exc.code, url, body) from None
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 4:
                    raise RuntimeError(f"Cannot reach Canvas at {url}: {exc}") from None
                last_error = exc
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Canvas request failed: {last_error}")

    def get(self, path: str, **params) -> Any:
        url = path
        if params:
            url += ("&" if "?" in path else "?") + _encode(params)
        payload, _ = self._request("GET", url)
        return payload

    def paged(self, path: str, **params) -> Iterator[dict]:
        """Follow Canvas Link-header pagination and yield each item."""
        params.setdefault("per_page", 100)
        url = path + ("&" if "?" in path else "?") + _encode(params)
        while url:
            payload, headers = self._request("GET", url)
            if isinstance(payload, list):
                yield from payload
            elif payload is not None:
                yield payload
            match = _NEXT_LINK.search(headers.get("Link", "") or headers.get("link", ""))
            url = match.group(1) if match else None

    def download(self, url: str, dest: Path) -> Path:
        """Fetch a Canvas file URL to disk.

        Submission attachment URLs are already signed and redirect to S3 or
        CloudFront, which rejects the request with 401 if a bearer token also
        rides along. So try unauthenticated first, then fall back to the token
        for plain /api/v1/files/:id/download URLs that do need it.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        tries = (False, True) if canvas_policy.same_host(self.base, url) else (False,)
        for use_token in tries:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", USER_AGENT)
            if use_token:
                canvas_policy.assert_token_host(self.base, url, self.allowed_hosts)
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as resp:
                    payload = resp.read()
                if not payload:
                    errors.append("empty response")
                    continue
                dest.write_bytes(payload)
                return dest
            except urllib.error.HTTPError as exc:
                errors.append(f"HTTP {exc.code}{' with token' if use_token else ''}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
        raise RuntimeError(f"could not download attachment ({'; '.join(errors)})")

    # --------------------------------------------------------------- reading
    def whoami(self) -> dict:
        return self.get("/users/self/profile")

    def courses(self, enrollment_types: list[str]) -> list[dict]:
        seen: dict[int, dict] = {}
        for role in enrollment_types:
            for course in self.paged(
                "/courses",
                enrollment_type=role,
                enrollment_state="active",
                state=["available", "unpublished"],
                include=["term", "total_students"],
            ):
                if isinstance(course, dict) and course.get("id"):
                    seen.setdefault(course["id"], course)
        return sorted(seen.values(), key=_course_sort_key, reverse=True)

    def assignments(self, course_id: int | str) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/assignments",
                               include=["assignment_visibility"], order_by="due_at"))

    def assignment(self, course_id: int | str, assignment_id: int | str) -> dict:
        return self.get(f"/courses/{course_id}/assignments/{assignment_id}")

    def students(self, course_id: int | str) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/users",
                               enrollment_type=["student"], enrollment_state=["active"]))

    def submissions(self, course_id: int | str, assignment_id: int | str) -> list[dict]:
        return list(self.paged(
            f"/courses/{course_id}/assignments/{assignment_id}/submissions",
            include=["user", "submission_comments", "rubric_assessment"],
        ))

    def submission_states(self, course_id: int | str, assignment_id: int | str) -> list[dict]:
        """Every submission, without the attachments, comments or rubric.

        Answering "who has turned nothing in" needs one field per student, and
        the full `submissions()` read pulls comment threads and rubric
        assessments for a whole class to get it.
        """
        return list(self.paged(
            f"/courses/{course_id}/assignments/{assignment_id}/submissions"))

    def discussion_view(self, course_id: int | str, topic_id: int | str) -> dict:
        return self.get(f"/courses/{course_id}/discussion_topics/{topic_id}/view")

    # --------------------------------------------------------------- writing
    def post_grade(self, course_id: int | str, assignment_id: int | str, user_id: int | str,
                   score: float | None = None, comment: str | None = None,
                   rubric: dict | None = None) -> dict:
        """Write one grade and/or comment. Callers must gate this themselves."""
        fields: list[tuple[str, str]] = []
        if score is not None:
            fields.append(("submission[posted_grade]", str(score)))
        if comment:
            fields.append(("comment[text_comment]", comment))
        for crit_id, entry in (rubric or {}).items():
            if entry.get("points") is not None:
                fields.append((f"rubric_assessment[{crit_id}][points]", str(entry["points"])))
            if entry.get("comments"):
                fields.append((f"rubric_assessment[{crit_id}][comments]", entry["comments"]))
        if not fields:
            raise ValueError("post_grade called with nothing to write")

        body = urllib.parse.urlencode(fields).encode("utf-8")
        payload, _ = self._request(
            "PUT",
            f"{self.base}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
            data=body,
            content_type="application/x-www-form-urlencoded",
        )
        return payload or {}

    def _json_body(self, method: str, path: str, body: dict) -> dict:
        """Send a JSON body. Used where Canvas takes an array of objects and
        form encoding would depend on the order of repeated bracket keys."""
        payload, _ = self._request(
            method, f"{self.base}/api/v1{path}",
            data=json.dumps(body).encode("utf-8"),
            content_type="application/json")
        return payload or {}

    def _form(self, method: str, path: str, fields: list[tuple[str, str]]) -> dict:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        payload, _ = self._request(
            method, f"{self.base}/api/v1{path}", data=body,
            content_type="application/x-www-form-urlencoded")
        return payload or {}

    # Only these assignment fields may be written. An instruction typed in a box
    # should not be able to reach anything else on the assignment, so the set is
    # named here rather than passed through from the caller.
    DATE_FIELDS = ("due_at", "unlock_at", "lock_at")

    def update_assignment(self, course_id: int | str, assignment_id: int | str,
                          dates: dict | None = None,
                          published: bool | None = None) -> dict:
        """Change an assignment's dates and/or publish state. Gate at the caller.

        `dates` values are UTC ISO strings, or None to clear a date.
        """
        fields: list[tuple[str, str]] = []
        for name in self.DATE_FIELDS:
            if dates and name in dates:
                fields.append((f"assignment[{name}]", dates[name] or ""))
        if published is not None:
            fields.append(("assignment[published]", "true" if published else "false"))
        if not fields:
            raise ValueError("update_assignment called with nothing to change")
        return self._form("PUT", f"/courses/{course_id}/assignments/{assignment_id}",
                          fields)

    def update_quiz(self, course_id: int | str, quiz_id: int | str,
                    dates: dict | None = None,
                    published: bool | None = None) -> dict:
        """The same change on a classic quiz.

        A quiz carries its own copies of these dates, and writing only the
        assignment can leave the quiz still locked. Callers that touch a quiz
        assignment should write both.
        """
        fields: list[tuple[str, str]] = []
        for name in self.DATE_FIELDS:
            if dates and name in dates:
                fields.append((f"quiz[{name}]", dates[name] or ""))
        if published is not None:
            fields.append(("quiz[published]", "true" if published else "false"))
        if not fields:
            raise ValueError("update_quiz called with nothing to change")
        return self._form("PUT", f"/courses/{course_id}/quizzes/{quiz_id}", fields)

    # ------------------------------------------------------------- quizzes
    def quizzes(self, course_id: int | str) -> list[dict]:
        """Every classic quiz in a course. New Quizzes do not appear here."""
        return list(self.paged(f"/courses/{course_id}/quizzes"))

    def quiz(self, course_id: int | str, quiz_id: int | str) -> dict:
        return self.get(f"/courses/{course_id}/quizzes/{quiz_id}")

    # Only these quiz fields may be written from the settings editor. Named
    # here rather than passed through, so nothing arriving in a request body
    # can widen the set.
    QUIZ_FIELDS = (
        "due_at", "unlock_at", "lock_at", "time_limit", "access_code",
        "allowed_attempts", "scoring_policy", "shuffle_answers",
        "one_question_at_a_time", "cant_go_back", "hide_results",
        "show_correct_answers", "one_time_results", "ip_filter", "published",
    )

    def update_quiz_settings(self, course_id: int | str, quiz_id: int | str,
                             changes: dict) -> dict:
        """Write a vetted set of quiz settings.

        Values arrive in the shapes Canvas wants: ISO UTC strings for dates,
        ints for minutes and attempts, bools for switches. `None` clears a
        field -- an empty string is how Canvas is told to drop a date, a
        password, or an IP filter.
        """
        fields: list[tuple[str, str]] = []
        for name in self.QUIZ_FIELDS:
            if name not in changes:
                continue
            value = changes[name]
            if value is None:
                fields.append((f"quiz[{name}]", ""))
            elif isinstance(value, bool):
                fields.append((f"quiz[{name}]", "true" if value else "false"))
            else:
                fields.append((f"quiz[{name}]", str(value)))
        if not fields:
            raise ValueError("update_quiz_settings called with nothing to change")
        return self._form("PUT", f"/courses/{course_id}/quizzes/{quiz_id}", fields)

    # ---------------------------------------------------- quiz accommodations
    # Canvas caps these itself; the same caps are checked before sending so a
    # bad number is reported against one student instead of failing a batch.
    MAX_EXTRA_TIME_MIN = 10080          # one week
    MAX_EXTRA_ATTEMPTS = 1000

    def quiz_extensions(self, course_id: int | str, quiz_id: int | str,
                        extensions: list[dict]) -> dict:
        """Give specific students extra time, extra attempts, or an unlock.

        One call carries every student for that quiz, which is what makes a
        blanket accommodation across a term affordable: one request per quiz
        rather than one per student per quiz.

        `extra_time` is extra MINUTES on top of the quiz's own limit -- not a
        multiplier, and not a new total.
        """
        rows = []
        for ext in extensions:
            uid = ext.get("user_id")
            if uid in (None, ""):
                raise ValueError("a quiz extension needs a user_id")
            row: dict = {"user_id": int(uid)}
            if ext.get("extra_time") is not None:
                minutes = int(ext["extra_time"])
                if not 0 <= minutes <= self.MAX_EXTRA_TIME_MIN:
                    raise ValueError(
                        f"extra time of {minutes} minutes is outside what "
                        f"Canvas accepts (0 to {self.MAX_EXTRA_TIME_MIN})")
                row["extra_time"] = minutes
            if ext.get("extra_attempts") is not None:
                attempts = int(ext["extra_attempts"])
                if not 0 <= attempts <= self.MAX_EXTRA_ATTEMPTS:
                    raise ValueError(
                        f"{attempts} extra attempts is outside what Canvas "
                        f"accepts (0 to {self.MAX_EXTRA_ATTEMPTS})")
                row["extra_attempts"] = attempts
            if ext.get("manually_unlocked") is not None:
                row["manually_unlocked"] = bool(ext["manually_unlocked"])
            if len(row) == 1:
                raise ValueError(f"nothing to change for user {uid}")
            rows.append(row)
        if not rows:
            raise ValueError("quiz_extensions called with nobody to extend")
        return self._json_body(
            "POST", f"/courses/{course_id}/quizzes/{quiz_id}/extensions",
            {"quiz_extensions": rows})

    def quiz_submissions(self, course_id: int | str, quiz_id: int | str) -> list[dict]:
        """Submission rows for a quiz, which is where Canvas keeps the
        extensions already granted. A quiz nobody has taken and nobody has an
        extension on returns nothing."""
        raw = self.get(f"/courses/{course_id}/quizzes/{quiz_id}/submissions",
                       per_page=100)
        if isinstance(raw, dict):
            return list(raw.get("quiz_submissions") or [])
        return list(raw or [])

    # ------------------------------------------------------- date overrides
    def assignment_overrides(self, course_id: int | str,
                             assignment_id: int | str) -> list[dict]:
        return list(self.paged(
            f"/courses/{course_id}/assignments/{assignment_id}/overrides"))

    def create_override(self, course_id: int | str, assignment_id: int | str,
                        student_ids: list, title: str,
                        dates: dict | None = None) -> dict:
        """A different due/unlock/lock window for named students."""
        ids = [str(int(u)) for u in student_ids if str(u).strip()]
        if not ids:
            raise ValueError("an override needs at least one student")
        fields = [("assignment_override[title]", title)]
        fields += [("assignment_override[student_ids][]", u) for u in ids]
        for name in self.DATE_FIELDS:
            if dates and name in dates:
                fields.append((f"assignment_override[{name}]", dates[name] or ""))
        return self._form(
            "POST", f"/courses/{course_id}/assignments/{assignment_id}/overrides",
            fields)

    def create_announcement(self, course_id: int | str, title: str, message: str,
                            delayed_post_at: str | None = None) -> dict:
        """Post a course announcement. Students see it as soon as it is live."""
        if not (title or "").strip() or not (message or "").strip():
            raise ValueError("an announcement needs a title and a message")
        fields = [("title", title), ("message", message),
                  ("is_announcement", "true")]
        if delayed_post_at:
            fields.append(("delayed_post_at", delayed_post_at))
        return self._form("POST", f"/courses/{course_id}/discussion_topics", fields)

    # ------------------------------------------------------------- the inbox
    # Reads of /conversations are student data, so only the grading-scoped
    # client reaches them; canvas_policy refuses the content scope outright.

    def conversations(self, scope: str = "", course_id=None, limit: int = 50) -> list[dict]:
        """The Canvas Inbox, newest first.

        `scope` is Canvas's own: empty for the inbox, or "unread", "archived",
        "sent". The list carries a one-line preview per thread, so counting
        what is waiting costs one call rather than one per thread.
        """
        params: dict = {"per_page": min(int(limit), 100)}
        if scope:
            params["scope"] = scope
        if course_id:
            params["filter[]"] = f"course_{course_id}"
        rows = []
        for row in self.paged("/conversations", **params):
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    def conversation(self, conversation_id: int | str, mark_read: bool = False) -> dict:
        """One thread with its messages.

        Reading does not mark it read unless asked. Somebody skimming this
        screen has not answered the student, and Canvas quietly clearing the
        unread flag would take away the only mark they had.
        """
        return self.get(f"/conversations/{conversation_id}",
                        auto_mark_as_read=("true" if mark_read else "false"))

    def reply_to_conversation(self, conversation_id: int | str, body: str,
                              recipients: list | None = None) -> dict:
        """Add one message to a thread that already exists."""
        if not (body or "").strip():
            raise ValueError("a reply needs a body")
        fields = [("body", body)]
        for who in (recipients or []):
            fields.append(("recipients[]", str(who)))
        return self._form("POST", f"/conversations/{conversation_id}/add_message", fields)

    def create_conversation(self, recipients: list, subject: str, body: str,
                            course_id: int | str | None = None) -> list:
        """Send one Canvas Inbox message to a list of students.

        Each recipient gets a private copy and cannot see who else was written
        to. That takes both flags: group_conversation alone would open a single
        thread where the whole class reads each other's replies, and
        bulk_message splits it back into one conversation per student. The pair
        is also what lifts Canvas's 100-recipient cap on a single call.
        """
        ids = [str(r).strip() for r in recipients if str(r).strip()]
        if not ids:
            raise ValueError("a message needs at least one recipient")
        if not (body or "").strip():
            raise ValueError("a message needs a body")
        fields = [("subject", (subject or "").strip()), ("body", body)]
        fields += [("recipients[]", u) for u in ids]
        if course_id:
            fields.append(("context_code", f"course_{course_id}"))
        fields += [("group_conversation", "true"), ("bulk_message", "true"),
                   ("force_new", "true")]
        payload = self._form("POST", "/conversations", fields)
        return payload if isinstance(payload, list) else [payload]

    # ----------------------------------------------------------- user files
    # Your own Canvas file area, not the course's. Course files ride along with
    # a course copy, so a semester rollover would carry last term's grading
    # drafts into the new shell; user files belong to the account and are
    # untouched by copy, export and sandbox cleanups.

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        """Hand the 3xx back instead of chasing it.

        Step two of an upload answers with a redirect to a confirmation URL that
        does need the token, while the upload POST itself must not carry one.
        Letting urllib follow it would send the request unauthenticated, and as
        a GET.
        """

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    def upload_user_file(self, name: str, payload: bytes,
                         folder: str = "canvas-grader",
                         content_type: str = "application/octet-stream") -> dict:
        """Put one file in your Canvas user files, replacing any of that name.

        Canvas takes three steps: ask for somewhere to put it, POST it there,
        then confirm. The middle step goes to pre-signed storage which rejects
        the request outright if a bearer token rides along, the same trap
        `download` documents for submission attachments.
        """
        offer = self._form("POST", "/users/self/files", [
            ("name", name),
            ("size", str(len(payload))),
            ("content_type", content_type),
            ("parent_folder_path", folder),
            ("on_duplicate", "overwrite"),
        ])
        url = (offer or {}).get("upload_url")
        if not url:
            raise RuntimeError(f"Canvas did not offer an upload slot for {name}: {offer}")

        body, ctype = _multipart(list((offer.get("upload_params") or {}).items()),
                                 "file", name, payload, content_type)
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", ctype)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")

        opener = urllib.request.build_opener(self._NoRedirect)
        timeout = max(self.timeout, 300)          # a Blender bundle is megabytes
        try:
            with opener.open(req, timeout=timeout) as resp:
                status, raw, location = resp.status, resp.read(), resp.headers.get("Location")
        except urllib.error.HTTPError as exc:
            status, raw, location = exc.code, exc.read(), exc.headers.get("Location")
            if status >= 400:
                raise CanvasError(status, url, raw.decode("utf-8", "replace")) from None

        if status in (301, 302, 303, 307, 308) and location:
            payload_back, _ = self._request("GET", location)
            return payload_back or {}
        text = raw.decode("utf-8", "replace")
        return json.loads(text) if text.strip() else {}

    def user_folder_files(self, folder: str = "canvas-grader") -> list[dict]:
        """Everything in one user-files folder. Empty when it does not exist."""
        try:
            found = self.get(f"/users/self/folders/by_path/{urllib.parse.quote(folder)}")
        except CanvasError as exc:
            if exc.status == 404:
                return []
            raise
        # by_path answers with every folder along the path; the last is the leaf.
        leaf = found[-1] if isinstance(found, list) and found else found
        if not isinstance(leaf, dict) or not leaf.get("id"):
            return []
        return list(self.paged(f"/folders/{leaf['id']}/files"))

    def read_file_bytes(self, url: str) -> bytes:
        """Fetch a Canvas file's content. Signed URLs refuse a bearer token, so
        try without one first and fall back, exactly as `download` does."""
        errors: list[str] = []
        tries = (False, True) if canvas_policy.same_host(self.base, url) else (False,)
        for use_token in tries:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", USER_AGENT)
            if use_token:
                canvas_policy.assert_token_host(self.base, url, self.allowed_hosts)
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(req, timeout=max(self.timeout, 300)) as resp:
                    data = resp.read()
                if data:
                    return data
                errors.append("empty response")
            except urllib.error.HTTPError as exc:
                errors.append(f"HTTP {exc.code}{' with token' if use_token else ''}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
        raise RuntimeError(f"could not read the file ({'; '.join(errors)})")

    def delete_file(self, file_id: int | str) -> dict:
        payload, _ = self._request("DELETE", f"{self.base}/api/v1/files/{file_id}")
        return payload or {}

    def user_quota(self) -> dict:
        """Bytes allowed and bytes used in your own file area."""
        return self.get("/users/self/files/quota") or {}

    # --------------------------------------------------------------- graphql
    def graphql(self, query: str, variables: dict | None = None) -> dict:
        canvas_policy.check_scope(self.scope, "POST", f"{self.base}/api/graphql")
        """One GraphQL call. Same bearer token, same retry/throttle handling."""
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        payload, _ = self._request("POST", f"{self.base}/api/graphql",
                                   data=body, content_type="application/json")
        payload = payload or {}
        errors = payload.get("errors") or []
        if errors:
            raise RuntimeError("Canvas GraphQL: " + "; ".join(
                str((e or {}).get("message") or e) for e in errors))
        return payload.get("data") or {}

    @staticmethod
    def _payload_errors(result: dict | None, what: str) -> None:
        errs = (result or {}).get("errors") or []
        if errs:
            raise RuntimeError(f"{what}: " + "; ".join(
                str((e or {}).get("message") or e) for e in errs))

    def post_policy(self, course_id: int | str, assignment_id: int | str | None = None) -> dict:
        """Whether grades post automatically or wait for a manual Post.

        Canvas keeps a policy on the course and, optionally, one on each
        assignment that overrides it. The effective policy for a push is the
        assignment's if it has one, else the course's, else Canvas's own
        default, which is automatic.
        """
        query = ("query($cid: ID!" + (", $aid: ID!" if assignment_id else "") + ") {\n"
                 "  course(id: $cid) { postPolicy { postManually } }\n"
                 + ("  assignment(id: $aid) { postPolicy { postManually } }\n" if assignment_id else "")
                 + "}")
        variables = {"cid": str(course_id)}
        if assignment_id:
            variables["aid"] = str(assignment_id)
        data = self.graphql(query, variables)
        course = ((data.get("course") or {}).get("postPolicy") or {}).get("postManually")
        assign = (((data.get("assignment") or {}).get("postPolicy") or {}).get("postManually")
                  if assignment_id else None)
        effective = assign if assign is not None else (course if course is not None else False)
        return {
            "course_manual": course,
            "assignment_manual": assign,
            "effective_manual": bool(effective),
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

    def set_course_post_policy(self, course_id: int | str, manual: bool) -> dict:
        """Course-wide. Canvas applies this to every assignment's policy too,
        which is what the gradebook's own setting does."""
        mutation = """mutation($cid: ID!, $m: Boolean!) {
          setCoursePostPolicy(input: {courseId: $cid, postManually: $m}) {
            postPolicy { postManually }
            errors { message }
          }
        }"""
        result = self.graphql(mutation, {"cid": str(course_id), "m": bool(manual)})
        self._payload_errors(result.get("setCoursePostPolicy"), "set course post policy")
        return (result.get("setCoursePostPolicy") or {}).get("postPolicy") or {}

    def set_assignment_post_policy(self, assignment_id: int | str, manual: bool) -> dict:
        mutation = """mutation($aid: ID!, $m: Boolean!) {
          setAssignmentPostPolicy(input: {assignmentId: $aid, postManually: $m}) {
            postPolicy { postManually }
            errors { message }
          }
        }"""
        result = self.graphql(mutation, {"aid": str(assignment_id), "m": bool(manual)})
        self._payload_errors(result.get("setAssignmentPostPolicy"), "set assignment post policy")
        return (result.get("setAssignmentPostPolicy") or {}).get("postPolicy") or {}

    def release_grades(self, assignment_id: int | str,
                       only_user_ids: list[str] | None = None,
                       hide: bool = False) -> dict:
        """The gradebook's Post (or Hide) button, for the whole assignment or
        for the given students. Canvas does the work asynchronously and hands
        back a Progress; see wait_progress."""
        name = "hideAssignmentGrades" if hide else "postAssignmentGrades"
        extra = "" if hide else ", gradedOnly: true"
        mutation = f"""mutation($aid: ID!, $ids: [ID!]) {{
          {name}(input: {{assignmentId: $aid, onlyStudentIds: $ids{extra}}}) {{
            progress {{ _id state }}
            errors {{ message }}
          }}
        }}"""
        ids = [str(u) for u in only_user_ids] if only_user_ids else None
        result = self.graphql(mutation, {"aid": str(assignment_id), "ids": ids})
        self._payload_errors(result.get(name), "hide grades" if hide else "post grades")
        return (result.get(name) or {}).get("progress") or {}

    def wait_progress(self, progress_id: int | str | None, timeout_s: int = 45) -> dict:
        """Poll a Canvas Progress until it settles or the timeout passes."""
        if not progress_id:
            return {"workflow_state": "unknown"}
        deadline = time.monotonic() + timeout_s
        info: dict = {}
        while time.monotonic() < deadline:
            info = self.get(f"/progress/{progress_id}") or {}
            if info.get("workflow_state") in ("completed", "failed"):
                return info
            time.sleep(1.0)
        return info


def _multipart(fields: list, file_field: str, filename: str,
               payload: bytes, content_type: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand.

    The client is standard library only, and Canvas's storage step will not take
    a urlencoded body. The ordering matters: every field Canvas handed back must
    precede the file part, or the upload is rejected.
    """
    boundary = "----courseforge" + secrets.token_hex(16)
    out = bytearray()
    for key, value in fields:
        out += f"--{boundary}\r\n".encode("utf-8")
        out += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
        out += str(value).encode("utf-8") + b"\r\n"
    out += f"--{boundary}\r\n".encode("utf-8")
    out += (f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n').encode("utf-8")
    out += f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    out += payload + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _encode(params: dict) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((f"{key}[]", str(v)) for v in value)
        elif value is not None:
            pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs)


def _course_sort_key(course: dict):
    term = (course.get("term") or {}).get("start_at") or ""
    return (term, str(course.get("name", "")))
