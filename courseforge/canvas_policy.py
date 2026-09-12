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
"""
from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse

SCOPES = ("grading", "content", "full")

# Anything that returns, lists, or edits people. /users/self is the instructor.
_DENY = re.compile(
    r"/(submissions|submission_summary|gradebook|grades|grade_change|grade_change_audit|"
    r"enrollments|users|students|observees|observers|analytics|conversations|"
    r"entries|entry_list|activity_stream|sis_imports|logins|profile|avatars|feeds|"
    r"recent_students|student_view|effective_due_dates|quiz_submissions|assessments|"
    r"rubric_assessments|peer_reviews|user_participants|gradebook_history|"
    r"outcome_results|outcome_rollups|late_policy|search_users)(/|\?|$)")
_SELF = re.compile(r"/users/self(/|\?|$)")
# What content work legitimately touches. Anything else under "content" is refused.
_ALLOW = re.compile(
    r"/(courses|accounts|pages|front_page|modules|items|assignments|assignment_groups|"
    r"quizzes|questions|discussion_topics|tabs|files|folders|external_tools|"
    r"content_exports|content_migrations|migration_issues|progress|rubrics|"
    r"rubric_associations|announcements|outcome_groups|outcomes|terms|settings|"
    r"features|blueprint_templates|content_licenses|usage_rights|todo|"
    r"course_nicknames|media_objects|by_path)(/|\?|$)")


class PolicyDenied(PermissionError):
    pass


def check_scope(scope: str, method: str, url: str) -> None:
    """Raise PolicyDenied if `scope` may not touch this Canvas URL."""
    if scope in ("grading", "full"):
        return
    path = urlparse(url).path if "://" in url else url.split("?", 1)[0]
    if _SELF.search(path):
        return
    if _DENY.search(path):
        raise PolicyDenied(
            f"Refused: {method} {path} reads or writes student data, and this part of "
            "CourseForge Studio works on course content only. Grading has its own, "
            "pseudonymised path.")
    if path.rstrip("/") in ("/api/v1", "/api/graphql") or path.endswith("/api/graphql"):
        raise PolicyDenied("Refused: GraphQL is reserved for the grading area.")
    if not _ALLOW.search(path):
        raise PolicyDenied(
            f"Refused: {method} {path} is not on the list of course-content endpoints. "
            "Add it to canvas_policy._ALLOW on purpose, not by accident.")


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
