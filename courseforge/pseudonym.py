"""Swap student identities for stable pseudonyms before text leaves the machine.

Best effort, not a guarantee: free-text PII (an unusual name written into an
essay, a name baked into a screenshot) can survive. The point is that the
roster mapping never leaves this machine, so a leak is not a roster leak.
"""
from __future__ import annotations

import re

# Structured identifiers worth scrubbing regardless of pseudonymization.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}\.[A-Za-z]\d{8,9}\b"), "[SISID]"),
    (re.compile(r"\b[A-Za-z]\d{8,9}\b"), "[USERID]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]


class Pseudonymizer:
    """Assigns S-001, S-002... in stable user-id order for one assignment."""

    def __init__(self, users: list[dict], enabled: bool = True):
        self.enabled = enabled
        self.by_user: dict[str, str] = {}
        self.identities: dict[str, dict] = {}
        for index, user in enumerate(sorted(users, key=lambda u: int(u.get("id", 0))), start=1):
            uid = str(user.get("id"))
            tag = f"S-{index:03d}"
            self.by_user[uid] = tag
            self.identities[tag] = {
                "user_id": uid,
                "name": user.get("name", ""),
                "sortable_name": user.get("sortable_name", ""),
            }

    def tag(self, user_id) -> str:
        uid = str(user_id)
        if not self.enabled:
            return uid
        return self.by_user.get(uid, f"U-{uid}")

    def label(self, user_id, name: str) -> str:
        """What the model is told to call this student."""
        return self.tag(user_id) if self.enabled else name

    def scrub(self, text: str, own_name: str = "") -> str:
        """Remove structured PII and, when enabled, the student's own name."""
        if not text:
            return text
        out = text
        for pattern, repl in PATTERNS:
            out = pattern.sub(repl, out)
        if self.enabled and own_name:
            out = _replace_name(out, own_name, "[NAME]")
        return out

    def scrub_roster(self, text: str) -> str:
        """Replace every roster name with that student's tag, then structured PII.

        Longest names first so "Jordan Alvarez" is not left as "S-001 Alvarez"
        after a shorter token already matched. Classmates in a discussion or a
        quoted filename have to become tags too, not only the author.
        """
        if not text:
            return text
        out = text
        if self.enabled:
            people = sorted(self.identities.values(),
                            key=lambda row: len(row.get("name") or ""), reverse=True)
            for row in people:
                name = row.get("name") or ""
                uid = row.get("user_id")
                tag = self.by_user.get(str(uid), "")
                if name and tag:
                    out = _replace_name(out, name, tag)
        return self.scrub(out)

    def map_json(self) -> dict:
        return {
            "note": "Pseudonym -> identity map. Local only. Never commit or transmit.",
            "identities": self.identities,
        }


def _replace_name(text: str, name: str, repl: str) -> str:
    """Replace a person's name tokens, case-insensitively."""
    if not name or not text:
        return text
    text = re.sub(rf"\b{re.escape(name)}\b", repl, text, flags=re.IGNORECASE)
    tokens = {t.strip(",.") for t in re.split(r"[\s,]+", name) if len(t.strip(",.")) >= 3}
    for token in sorted(tokens, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(token)}\b", repl, text, flags=re.IGNORECASE)
    return text


def _strip_name(text: str, name: str) -> str:
    """Replace the student's own name tokens with [NAME], case-insensitively."""
    return _replace_name(text, name, "[NAME]")
