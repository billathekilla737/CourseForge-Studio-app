"""Does every syllabus carry the statements the college requires?

An accreditation visit or a grade appeal both start in the same place: what did
the syllabus say. Checking six statements across five courses by hand is twenty
minutes of reading that nobody does in week one, and the one term it is skipped
is the term somebody needs it.

The list is data, not code. Every college requires a different set in different
words, so `knowledge/syllabus-policies.json` holds the defaults and
`syllabus_policies_path` in config.json points at your own copy. Editing the
list must never mean editing this file.

Two things this deliberately does not do.

**It does not judge the wording.** A match means the syllabus mentions the
subject, not that the statement is the approved one or that it is current. The
report says "mentions disability services", never "complies", because only
somebody holding the college's own catalogue can say the second thing.

**It does not write anything.** Reading a syllabus is a read, and the fix for a
missing statement is a decision about policy language that belongs to a person.
The report ends at a list and a link to each syllabus.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent / "knowledge" / "syllabus-policies.json"


def load(cfg=None) -> list[dict]:
    """The policy list, from config.json's copy if there is one."""
    path = Path(getattr(cfg, "syllabus_policies_path", "") or DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    out = []
    for row in (raw.get("policies") or []):
        if row.get("id") and row.get("any"):
            out.append({"id": str(row["id"]), "label": row.get("label") or row["id"],
                        "why": row.get("why") or "", "any": list(row["any"])})
    return out


def plain(body: str) -> str:
    """Syllabus text with the markup out of the way."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", str(body or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s{2,}", " ", html.unescape(text)).strip()


def check_body(body: str, policies: list[dict] | None = None, cfg=None) -> dict:
    """Which statements the syllabus mentions, and what it does not."""
    policies = policies if policies is not None else load(cfg)
    text = plain(body)
    rows = []
    for policy in policies:
        found, excerpt = None, ""
        for pattern in policy["any"]:
            try:
                hit = re.search(pattern, text, re.I)
            except re.error:
                continue
            if hit:
                found = pattern
                start = max(0, hit.start() - 60)
                excerpt = ("…" if start else "") + text[start:hit.end() + 90].strip() + "…"
                break
        rows.append({"id": policy["id"], "label": policy["label"],
                     "why": policy["why"], "present": bool(found),
                     "matched": found or "", "excerpt": excerpt})
    missing = [r for r in rows if not r["present"]]
    return {
        "checked": len(rows),
        "present": len(rows) - len(missing),
        "missing": [r["label"] for r in missing],
        "rows": rows,
        "empty": not text,
        "words": len(text.split()),
    }


def check_course(app, course_id, cfg=None) -> dict:
    """One course. A read: the content client is enough, and nothing is written."""
    detail = app.content.course_detail(course_id, include=["syllabus_body"])
    body = detail.get("syllabus_body") or ""
    out = check_body(body, cfg=cfg or app.cfg)
    name = detail.get("name") or str(course_id)
    out.update({
        "course_id": str(course_id),
        "course": name,
        "url": "%s/courses/%s/assignments/syllabus" % (app.cfg.base_url, course_id),
    })
    if out["empty"]:
        out["summary"] = "There is no syllabus in this course at all."
    elif not out["missing"]:
        out["summary"] = ("The syllabus mentions all %d required statements."
                          % out["checked"])
    else:
        out["summary"] = ("The syllabus does not mention %s."
                          % _join(out["missing"]))
    return out


def check_many(app, course_ids: list, cfg=None, log=lambda *_a, **_k: None) -> dict:
    """Every course you picked, as the one table somebody can act on."""
    ids = [str(c) for c in (course_ids or []) if str(c).strip()]
    if not ids:
        raise ValueError("Pick at least one course to check.")
    policies = load(cfg or app.cfg)
    rows, failed = [], []
    for index, cid in enumerate(ids, start=1):
        log("%d/%d reading the syllabus" % (index, len(ids)), index - 1, len(ids))
        try:
            rows.append(check_course(app, cid, cfg=cfg))
        except Exception as exc:  # noqa: BLE001
            failed.append({"course_id": cid,
                           "error": "%s: %s" % (type(exc).__name__, exc)})
        log("%d/%d done" % (index, len(ids)), index, len(ids))

    # Per policy, across every course: the shape a dean asks for. A course with
    # no syllabus at all is its own finding and is said in the summary; counted
    # here it would show as missing every statement, and "most often missing:
    # disability services" would be the wrong sentence about the wrong problem.
    with_syllabus = [r for r in rows if not r["empty"]]
    by_policy = []
    for policy in policies:
        misses = [r["course"] for r in with_syllabus
                  if not next((x["present"] for x in r["rows"]
                               if x["id"] == policy["id"]), True)]
        by_policy.append({"id": policy["id"], "label": policy["label"],
                          "why": policy["why"],
                          "missing_in": misses, "missing": len(misses),
                          "present": len(with_syllabus) - len(misses)})
    clean = [r for r in rows if not r["missing"] and not r["empty"]]
    return {
        "courses": rows, "failed": failed, "by_policy": by_policy,
        "no_syllabus": [r["course"] for r in rows if r["empty"]],
        "checked": len(rows), "clean": len(clean),
        "summary": _summary(rows, clean, by_policy),
        "note": ("A match means the syllabus mentions the subject. It does not "
                 "mean the wording is the college's approved statement, or that "
                 "it is current -- only somebody holding the catalogue can say "
                 "that. Nothing here changes a syllabus."),
    }


def _summary(rows, clean, by_policy) -> str:
    if not rows:
        return "Nothing was checked."
    empty = [r["course"] for r in rows if r["empty"]]
    worst = sorted([p for p in by_policy if p["missing"]],
                   key=lambda p: -p["missing"])
    parts = ["%d of %d syllabi mention every required statement."
             % (len(clean), len(rows))]
    if worst:
        parts.append("Most often missing: %s (%d course%s)."
                     % (worst[0]["label"], worst[0]["missing"],
                        "" if worst[0]["missing"] == 1 else "s"))
    if empty:
        parts.append("%s %s no syllabus at all."
                     % (_join(empty), "has" if len(empty) == 1 else "have"))
    return " ".join(parts)


def _join(items: list) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]
