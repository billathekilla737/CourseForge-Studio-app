"""Strip inline "box" borders used as emphasis from every HTML body in a
course (a port of Remove-BorderedBoxes.ps1).

    <span style="border: 1px solid #d7dce3">some sentence</span>

A border on an INLINE span fragments when the text wraps, so a sentence ends
up in a thin box with ragged open edges, and it signals emphasis by decoration
alone, which a screen reader announces as nothing. It is stray editor
formatting, usually pasted from Word or an older course, and it is course-wide
when it appears at all.

Surgical by design:
    style is ONLY the border      -> unwrap the span (tags dropped, content kept)
    style has other declarations  -> delete just the border declaration, keep
                                     the span and its remaining styling

Never touched: <div> cards and heroes (the card component legitimately uses
the same grey border) and pills (gold ring). Only SPANS are considered.

Covers pages, discussions and announcements, quiz descriptions, quiz question
text, assignment descriptions (shells skipped) and the syllabus. Every write is
gated on the visible text being identical afterwards.
"""
from __future__ import annotations

import re
from collections import Counter

from . import restyle
from .workdir import describe_counts, kind_label, strip_theme

SPAN = re.compile(r'<span([^>]*)style="([^"]*)"([^>]*)>', re.I)
SPAN_EDGE = re.compile(r"<span\b|</span\s*>", re.I)


def default_color() -> str:
    return restyle.C["hairline"]


def border_pattern(color: str | None = None):
    return re.compile(r"border\s*:\s*1px\s+solid\s+" + re.escape(color or default_color()) + r"\s*;?", re.I)


def count_boxes(html: str, color: str | None = None) -> int:
    if not html:
        return 0
    bd = border_pattern(color)
    return sum(1 for m in SPAN.finditer(html) if bd.search(m.group(2)))


def fix_boxes(html: str, color: str | None = None) -> tuple[str, str]:
    """Returns (html, note). note is non-empty when a span was left alone."""
    bd = border_pattern(color)
    note = ""
    while True:
        target = None
        for m in SPAN.finditer(html):
            if bd.search(m.group(2)):
                target = m
                break
        if not target:
            break
        reduced = bd.sub("", target.group(2)).strip()
        reduced = re.sub(r"^\s*;\s*", "", reduced)
        reduced = re.sub(r";\s*;", ";", reduced).strip()
        if re.match(r"^[\s;]*$", reduced):
            # unwrap: walk to the MATCHING </span> with a depth counter. A
            # non-greedy regex would stop at the first inner </span>.
            after_open = target.end()
            depth, pos, cs, ce = 1, after_open, -1, -1
            while depth > 0:
                nxt = SPAN_EDGE.search(html, pos)
                if not nxt:
                    break
                if nxt.group(0).startswith("</"):
                    depth -= 1
                else:
                    depth += 1
                pos = nxt.end()
                if depth == 0:
                    cs, ce = nxt.start(), nxt.end()
            if cs < 0:
                note = "unbalanced span; body left alone"
                break
            html = html[:target.start()] + html[after_open:cs] + html[ce:]
        else:
            new_tag = '<span%sstyle="%s"%s>' % (target.group(1), reduced, target.group(3))
            html = html[:target.start()] + new_tag + html[target.end():]
    return html, note


# ------------------------------------------------------------ the sweep

def targets(client, course_id):
    """Every body the sweep covers, as rows the read/write helpers understand."""
    for entry in client.iter_content_bodies(course_id):
        yield {"kind": entry["kind"], "id": entry["id"], "key": entry.get("key"),
               "title": entry.get("title") or "", "body": entry.get("body") or "",
               "published": entry.get("published")}
    for d in client.discussions(course_id, announcements=True):
        yield {"kind": "announcement", "id": d["id"], "key": "announcement_%s" % d["id"],
               "title": d.get("title") or "", "body": d.get("message") or "",
               "published": d.get("published")}
    for q in client.quizzes_content(course_id):
        for qq in client.quiz_questions(course_id, q["id"]):
            yield {"kind": "question", "id": qq["id"], "quiz_id": q["id"],
                   "key": "question_%s_%s" % (q["id"], qq["id"]),
                   "title": "[%s] %s" % (q.get("title") or "", qq.get("question_name") or ""),
                   "body": qq.get("question_text") or "", "published": q.get("published")}


def read_row(client, course_id, row: dict) -> str:
    kind = row["kind"]
    if kind == "announcement":
        return client.discussion(course_id, row["id"]).get("message") or ""
    if kind == "question":
        for qq in client.quiz_questions(course_id, row["quiz_id"]):
            if str(qq.get("id")) == str(row["id"]):
                return qq.get("question_text") or ""
        return ""
    return client.read_content_body(course_id, kind, row["id"])


def write_row(client, course_id, row: dict, body: str):
    kind = row["kind"]
    if kind == "announcement":
        return client.update_discussion(course_id, row["id"], message=body)
    if kind == "question":
        return client.update_quiz_question(course_id, row["quiz_id"], row["id"],
                                           {"question_text": body})
    return client.write_content_body(course_id, kind, row["id"], body)


def scan_course(client, course_id, color: str | None = None, log=None) -> dict:
    color = color or default_color()
    items, skipped = [], []
    for row in targets(client, course_id):
        n = count_boxes(row["body"], color)
        if n == 0:
            continue
        before = strip_theme(row["body"])
        after, note = fix_boxes(before, color)
        label = "%s: %s" % (kind_label(row["kind"]), row["title"])
        item = {"key": row["key"], "kind": row["kind"], "id": row["id"],
                "quiz_id": row.get("quiz_id"), "title": row["title"], "label": label,
                "published": row.get("published"), "boxes": n,
                "boxes_after": count_boxes(after, color), "before": before, "after": after,
                "would_change": after != before, "skipped_reason": ""}
        if note:
            item["skipped_reason"] = note
        elif not restyle.same_reader_text(before, after):
            item["skipped_reason"] = "visible text would change; left alone"
        (skipped if item["skipped_reason"] else items).append(item)
        if log:
            log("%s: %d box(es)" % (label, n))
    counts = Counter(it["kind"] for it in items)
    return {"course_id": str(course_id), "color": color, "items": items, "skipped": skipped,
            "change_count": len(items), "box_count": sum(it["boxes"] for it in items),
            "counts": dict(counts), "phrase": describe_counts(counts),
            "changes": [{"key": it["key"], "label": it["label"],
                         "from": "%d box(es)" % it["boxes"], "to": "%d" % it["boxes_after"]}
                        for it in items]}


def sentence(plan: dict, course_label: str) -> str:
    return ("Remove %d inline bordered-box spans (%s) from %s in %s. Text inside the boxes "
            "stays exactly where it is; only the border goes. Modules and publish state "
            "are not touched." % (plan["box_count"], plan["color"], plan["phrase"], course_label))


def apply_plan(client, course_id, plan: dict, log=None) -> dict:
    written, errors, skipped = [], [], []
    rows = plan["items"]
    first = True
    for i, it in enumerate(rows, 1):
        try:
            live = strip_theme(read_row(client, course_id, it))
        except Exception as exc:  # noqa: BLE001
            errors.append({"key": it["key"], "label": it["label"], "error": str(exc)[:300]})
            continue
        if live != it["before"]:
            skipped.append({"key": it["key"], "label": it["label"],
                            "reason": "changed in Canvas since the plan; run it again"})
            continue
        try:
            write_row(client, course_id, it, it["after"])
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None)
            if first and status == 403:
                raise PermissionError(
                    "The first write (%s) was refused with 403, so the course is probably "
                    "write-locked. Nothing has been changed." % it["label"]) from exc
            first = False
            errors.append({"key": it["key"], "label": it["label"], "error": str(exc)[:300]})
            continue
        first = False
        rec = {"key": it["key"], "kind": it["kind"], "label": it["label"], "boxes": it["boxes"],
               "ok": True, "issues": []}
        try:
            back = strip_theme(read_row(client, course_id, it))
            if not restyle.same_reader_text(back, it["after"]):
                if not (it["kind"] in ("quiz", "question") and it.get("published") is False):
                    rec["issues"].append("visible text differs on the live course")
            elif count_boxes(back, plan["color"]) > it["boxes_after"]:
                rec["issues"].append("boxes still present on the live course")
        except Exception as exc:  # noqa: BLE001
            rec["issues"].append("could not fetch it back: %s" % str(exc)[:160])
        rec["ok"] = not rec["issues"]
        written.append(rec)
        if log:
            log("%s %s" % ("changed" if rec["ok"] else "CHECK", it["label"]), i, len(rows))
    return {"written": written, "written_count": len(written), "errors": errors,
            "skipped": skipped, "boxes_removed": sum(r["boxes"] for r in written),
            "live_fails": sum(1 for r in written if not r["ok"])}
