"""Two rules every Canvas request obeys, enforced in the client, not in a prompt.

1. **The token never leaves the Canvas host.** A request that carries the bearer
   token must be https and must go to the configured Canvas host (or a host on
   the allow-list in config.json, for a self-hosted Canvas). Pre-signed file
   URLs on S3/CloudFront are fetched without the token.

2. **Student data is off limits to content work.** The old CourseForge shipped a
   PreToolUse hook that pattern-matched command text for student-data endpoints.
   That guard is gone upstream, and a text guard never saw the real request
   anyway. Here the check sits on the URL the client is about to send: a client
   opened with scope "content" (every area except grading) cannot reach
   submissions, grades, enrollments, users, analytics, conversations and the
   rest of the deny list. The grading area opens its client with scope
   "grading" and is the sanctioned path. Fail closed: an unknown endpoint under
   "content" scope must match the allow-list or it is refused.

   Fail closed means four things here, each of which was once a hole. The path
   is percent-decoded before it is read, so `/%75sers` is `/users`. The query
   string is read too, because `?include[]=students` turns a harmless section
   listing into a roster. The instructor's own account is an allow-list, not a
   free pass, because `/users/self/activity_stream` is every student post in
   every course. And the allow-list is matched on the last segment of the path,
   because "courses" appearing early in a URL says nothing about what comes
   after it.
"""
from __future__ import annotations

import fnmatch
import re
from urllib.parse import parse_qs, unquote, urlparse

SCOPES = ("grading", "content", "full")

# Anything that returns, lists, or edits people.
_DENY = re.compile(
    r"/(submissions|submission_summary|gradebook|grades|grade_change|grade_change_audit|"
    r"enrollments|users|students|observees|observers|analytics|conversations|"
    r"entries|entry_list|view|activity_stream|sis_imports|logins|profile|avatars|feeds|"
    r"recent_students|student_view|effective_due_dates|quiz_submissions|assessments|"
    r"rubric_assessments|peer_reviews|user_participants|gradebook_history|"
    r"gradeable_students|reports|statistics|extensions|overrides|collaborations|"
    r"module_item_sequence|groups|group_categories|memberships|"
    r"outcome_results|outcome_rollups|late_policy|search_users|search)(/|\?|$)")

# The instructor's own account: only what the Studio keeps there.
_SELF_ALLOW = re.compile(
    r"^(/api/v1)?/users/self(/profile|/files(/.*)?|/folders(/.*)?|/todo|/settings|"
    r"/course_nicknames(/.*)?|/communication_channels)?/?$")

# The last segment of a path content work legitimately reaches. A number is a
# resource id; a dotted name is a file or a page slug such as "week-1.html".
_ALLOW_LEAF = re.compile(
    r"^(courses|accounts|pages|front_page|modules|items|assignments|assignment_groups|"
    r"quizzes|questions|discussion_topics|tabs|files|folders|external_tools|"
    r"content_exports|content_migrations|migration_issues|progress|rubrics|"
    r"rubric_associations|announcements|outcome_groups|outcomes|terms|settings|"
    r"features|blueprint_templates|content_licenses|usage_rights|todo|"
    r"course_nicknames|media_objects|by_path|duplicate|reorder|relock|"
    r"bulk_update|select_content|syllabus|flags|enabled|public_url|"
    r"\d+|[^/]*[.\-][^/]*)$")
# And the path has to be about one of these things at all.
_ALLOW_ROOT = re.compile(
    r"/(courses|accounts|users/self|files|folders|progress|content_exports|"
    r"content_migrations|outcomes|outcome_groups|terms|announcements|by_path)(/|$)")

# Query parameters that pull people into an otherwise harmless listing:
# ?include[]=students on a section, ?include[]=assessments on a rubric.
_QUERY_DENY = re.compile(
    r"^(students?|users?|user_ids?|enrollments?|assessments|submissions?|"
    r"graded_submissions_exist|observed_users|current_grading_period_scores|"
    r"total_scores|peer_reviews|assignee_ids?)$", re.I)


class PolicyDenied(PermissionError):
    pass


def _refuse(method: str, path: str, why: str) -> None:
    raise PolicyDenied(
        f"Refused: {method} {path} {why}, and this part of CourseForge Studio works "
        "on course content only. Grading has its own, pseudonymised path.")


def check_scope(scope: str, method: str, url: str) -> None:
    """Raise PolicyDenied if `scope` may not touch this Canvas URL."""
    if scope in ("grading", "full"):
        return
    parsed = urlparse(url if "://" in url else "https://x" + url)
    # Twice: a doubly-encoded segment is decoded once by the server and once
    # by Canvas, so it has to be read the way Canvas will read it.
    path = unquote(unquote(parsed.path))
    query = parse_qs(unquote(parsed.query), keep_blank_values=True)

    for key, values in query.items():
        bare = re.sub(r"\[.*\]$", "", key)
        if _QUERY_DENY.match(bare) or any(_QUERY_DENY.match(v) for v in values):
            _refuse(method, path + "?" + parsed.query, "asks Canvas to include people")

    if re.match(r"^(/api/v1)?/users/self(/|$)", path):
        if _SELF_ALLOW.match(path):
            return
        _refuse(method, path, "reaches beyond the instructor's own files and profile")
    if _DENY.search(path):
        _refuse(method, path, "reads or writes student data")
    if path.rstrip("/") in ("/api/v1", "/api/graphql") or path.endswith("/api/graphql"):
        raise PolicyDenied("Refused: GraphQL is reserved for the grading area.")
    segments = path.rstrip("/").split("/")
    leaf, parent = segments[-1], (segments[-2] if len(segments) > 1 else "")
    # A page slug, a tab id or a folder path is whatever the author named it.
    named = parent in ("pages", "tabs", "by_path", "course_nicknames")
    if (not named and not _ALLOW_LEAF.match(leaf)) or not _ALLOW_ROOT.search(path):
        raise PolicyDenied(
            f"Refused: {method} {path} is not on the list of course-content endpoints. "
            "Add it to canvas_policy._ALLOW_LEAF on purpose, not by accident.")


def assert_token_host(base_url: str, url: str, allowed_hosts: list[str] | None = None) -> None:
    """A request that will carry the bearer token must stay on the Canvas host."""
    target = urlparse(url)
    home = urlparse(base_url)
    if target.scheme != "https":
        raise PolicyDenied(f"Refused: the Canvas token only travels over https ({url}).")
    host = (target.hostname or "").lower()
    if host == (home.hostname or "").lower():
        return
    for pattern in allowed_hosts or []:
        if fnmatch.fnmatch(host, pattern.lower()):
            return
    raise PolicyDenied(
        f"Refused: {host} is not your Canvas host ({home.hostname}). The token is not "
        "sent anywhere else. If your school runs Canvas on its own domain, add it to "
        "\"canvas_hosts\" in config.json.")


def same_host(base_url: str, url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == (urlparse(base_url).hostname or "").lower()
