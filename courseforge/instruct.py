"""Turn a typed instruction into a plan of course changes, for a human to approve.

"Unlock my test today" is a clear enough sentence and a genuinely useful thing to
be able to say. Acting on it is a write to a live course that students are
sitting in, so the shape here is deliberate:

* **The model proposes; it does not act.** It returns operations from a fixed
  vocabulary, each naming a course and assignment by id. It cannot name an
  endpoint, a field or a course that was not put in front of it.
* **Every operation is validated here** against the schedule the instructor is
  looking at. An id that is not in that list is dropped, a field outside the
  allow list is dropped, and a date that does not parse is dropped -- each with
  a reason the page can show, rather than being quietly skipped.
* **Nothing is applied by planning.** The plan is a preview with before and
  after values; applying it is a separate, confirmed step that still has to get
  past `allow_canvas_writes`.

Dates arrive as local wall-clock strings ("2026-09-09T23:59") because the
browser is the only part of this system that reliably knows the instructor's
timezone; it converts them to UTC before anything is applied.
"""
from __future__ import annotations

import re
from datetime import datetime
from html import escape as html_escape

from .style import HUMANIZE_RULES

# The whole vocabulary. Anything else the model returns is refused by name.
OPS = ("set_dates", "publish", "unpublish", "announce")

DATE_KEYS = ("unlock_at", "due_at", "lock_at")

# A local wall-clock stamp, no offset: "2026-09-09T23:59" or with seconds.
LOCAL_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?$")

FIELD_WORDS = {"unlock_at": "opens", "due_at": "due", "lock_at": "closes"}


PLAN_SYSTEM = """You turn one sentence from a college instructor into a small list of
changes to their Canvas courses, for them to approve before anything happens.

You are not carrying the change out and you have no other abilities. You return
operations from the vocabulary you were given, naming assignments by the ids in
the list you were shown, and nothing else.

Be literal. "Unlock my test today" means set the unlock date on that one test to
today; it does not mean move the due date, publish anything, or tell the class.
If the instruction is ambiguous about which assignment, ask instead of choosing.
If it asks for something outside the vocabulary, say so in "refused" rather than
finding a way around it.

These are live courses with students in them. A wrong date is a real problem for
real people, so prefer asking a question over a confident guess."""


def _local(value) -> str | None:
    """A local wall-clock stamp, normalised, or None if it is not one."""
    if value in (None, "", "null"):
        return None
    text = str(value).strip().replace(" ", "T")
    # Tolerate a trailing Z or offset by dropping it: the browser owns the zone,
    # and a model guessing an offset is a good way to move a deadline by hours.
    text = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", text)
    if not LOCAL_STAMP.match(text):
        return None
    if len(text) == 16:
        text += ":00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return None
    return text


def plan_prompt(instruction: str, items: list[dict], courses: list[dict],
                now_local: str, zone: str) -> str:
    """The prompt: the instruction, plus exactly the rows it may act on."""
    lines = [
        "# What the instructor typed",
        instruction.strip(), "",
        f"# Right now, where they are: {now_local} ({zone})", "",
        "# Their courses",
    ]
    for course in courses:
        lines.append(f"- course_id {course.get('course_id')}: "
                     f"{course.get('label') or course.get('code')}")
    lines += ["", "# Every assignment you may act on",
              "Each line is: assignment_id | course_id | course | type | name | "
              "opens | due | closes | published"]
    for item in items:
        lines.append(
            f"- {item.get('assignment_id')} | {item.get('course_id')} | "
            f"{item.get('course_label') or item.get('course_code')} | "
            f"{item.get('kind_label')} | {item.get('name')} | "
            f"{item.get('unlock_at') or '-'} | {item.get('due_at') or '-'} | "
            f"{item.get('lock_at') or '-'} | "
            f"{'yes' if item.get('published') else 'no'}")
    lines += ["", """# What to return

Return ONLY this JSON object:
{
  "understood": "<one sentence saying what you are about to do, in plain words>",
  "operations": [
    { "op": "set_dates", "course_id": "<id>", "assignment_id": "<id>",
      "unlock_at": "<YYYY-MM-DDTHH:MM or null to clear>",
      "due_at": "<same, omit if not changing>",
      "lock_at": "<same, omit if not changing>",
      "why": "<short reason, shown to the instructor>" },
    { "op": "publish" | "unpublish", "course_id": "<id>",
      "assignment_id": "<id>", "why": "<short reason>" },
    { "op": "announce", "course_id": "<id>", "title": "<subject line>",
      "message": "<the announcement, plain sentences>",
      "why": "<short reason>" }
  ],
  "questions": ["<anything genuinely ambiguous that changes what you would do>"],
  "refused": "<empty, or why you are not doing this>"
}

Rules:
- Times are the instructor's LOCAL wall clock. Never add a timezone or an
  offset, and never convert. "today" means the date given above.
- Only touch assignments from the list. Use their ids exactly.
- If the instruction names something that is not in the list, do not guess a
  near match: leave operations empty and say so in "questions".
- If more than one assignment could be meant, list them in "questions" rather
  than picking one.
- Never delete anything, never change points, never touch a grade. You have no
  operation for those, and asking for one is a refusal.
- Keep the operation list to exactly what was asked. Do not add helpful extras;
  an announcement is only included if they asked for one."""]
    return "\n".join(lines)


def validate(data: dict, items: list[dict], courses: list[dict]) -> dict:
    """Check a model plan against reality. Returns the plan the page may show."""
    by_assignment = {str(i.get("assignment_id")): i for i in items}
    course_ids = {str(c.get("course_id")) for c in courses}
    course_names = {str(c.get("course_id")): (c.get("label") or c.get("code"))
                    for c in courses}

    ops, rejected = [], []
    for raw in (data.get("operations") or [])[:25]:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "").strip()
        if op not in OPS:
            rejected.append({"why": f"unknown operation {op!r}", "raw": str(raw)[:120]})
            continue

        if op == "announce":
            cid = str(raw.get("course_id") or "")
            title = str(raw.get("message_title") or raw.get("title") or "").strip()
            message = str(raw.get("message") or "").strip()
            if cid not in course_ids:
                rejected.append({"why": f"course {cid or '(missing)'} is not one of "
                                        "yours", "raw": title[:80]})
                continue
            if not title or not message:
                rejected.append({"why": "announcement had no title or no message",
                                 "raw": title[:80]})
                continue
            ops.append({"op": op, "course_id": cid,
                        "course_label": course_names.get(cid, cid),
                        "title": title, "message": message,
                        "why": str(raw.get("why") or "")})
            continue

        aid = str(raw.get("assignment_id") or "")
        item = by_assignment.get(aid)
        if not item:
            rejected.append({"why": f"assignment {aid or '(missing)'} is not in the "
                                    "schedule you are looking at",
                             "raw": str(raw.get("why") or "")[:80]})
            continue
        cid = str(raw.get("course_id") or item.get("course_id"))
        if cid != str(item.get("course_id")):
            rejected.append({"why": f"assignment {aid} does not belong to course {cid}",
                             "raw": item.get("name", "")[:80]})
            continue

        entry = {"op": op, "course_id": cid, "assignment_id": aid,
                 "course_label": item.get("course_label") or item.get("course_code"),
                 "name": item.get("name"), "kind_label": item.get("kind_label"),
                 "quiz_id": item.get("quiz_id"),
                 "why": str(raw.get("why") or "")}

        if op in ("publish", "unpublish"):
            want = op == "publish"
            if bool(item.get("published")) == want:
                rejected.append({"why": f"{item.get('name')} is already "
                                        f"{'published' if want else 'unpublished'}",
                                 "raw": ""})
                continue
            entry["published"] = want
            ops.append(entry)
            continue

        # set_dates. Two shapes reach this: the model returns the dates as flat
        # keys, and an operation coming back from the page for approval carries
        # them nested under "dates" (plus the UTC the browser worked out). Both
        # go through the same checks, because this runs again before anything is
        # applied and must not be bypassable by reshaping the request.
        nested = raw.get("dates") if isinstance(raw.get("dates"), dict) else None
        source = nested if nested is not None else raw
        changes = {}
        for key in DATE_KEYS:
            if key not in source:
                continue
            if source[key] in (None, "", "null"):
                changes[key] = None          # an explicit clear
                continue
            stamp = _local(source[key])
            if stamp is None:
                rejected.append({"why": f"could not read {key} "
                                        f"{str(source[key])[:30]!r} as a date and time",
                                 "raw": item.get("name", "")[:80]})
                continue
            changes[key] = stamp
        if not changes:
            rejected.append({"why": f"no usable date change for "
                                    f"{item.get('name')}", "raw": ""})
            continue
        entry["dates"] = changes
        entry["before"] = {key: item.get(key) for key in DATE_KEYS}
        # Carry the browser's UTC through, but only for fields that survived the
        # checks above: an approved field cannot be swapped for another one.
        supplied = raw.get("dates_utc")
        if isinstance(supplied, dict):
            entry["dates_utc"] = {key: supplied[key] for key in changes
                                  if key in supplied}
        ops.append(entry)

    return {
        "understood": str(data.get("understood") or ""),
        "operations": ops,
        "rejected": rejected,
        "questions": [str(q) for q in (data.get("questions") or [])][:6],
        "refused": str(data.get("refused") or ""),
    }


def describe(op: dict) -> str:
    """One line an instructor can check, for the confirmation dialog."""
    if op["op"] == "announce":
        return f"post an announcement to {op['course_label']}: \"{op['title']}\""
    what = f"{op.get('course_label')} — {op.get('name')}"
    if op["op"] in ("publish", "unpublish"):
        return f"{op['op']} {what}"
    parts = []
    for key, value in (op.get("dates") or {}).items():
        word = FIELD_WORDS.get(key, key)
        parts.append(f"{word} {value.replace('T', ' ') if value else 'cleared'}")
    return f"{what}: set {', '.join(parts)}"


# ---------------------------------------------------------------- announcements
ANNOUNCE_SYSTEM = f"""You write short course announcements for a community college
instructor. The students reading them are busy and half of them are on a phone.

Rules:
- Lead with what they have to do and when. Never open with a greeting or a
  preamble about the course.
- Plain sentences. No emoji, no exclamation marks, no "Don't forget!", no
  "Good luck!", no motivational sign-off.
- Say the date and time in words a person reads: "Wednesday, September 10, by
  11:59 PM", not an ISO stamp.
- Only state facts you were given. Do not invent a location, a password, a
  proctor, a vendor, a webcam, an ID check, a lock-down browser, a chapter
  list or a length. If a detail matters and you were not told it, leave it
  out rather than guessing.
- Four short sentences is plenty. One paragraph, no headings, no bullet lists
  unless there are genuinely separate steps.

{HUMANIZE_RULES}
"""

# Titles and bodies that are how this course says to take a test — not the
# Studio app, and not a guess.
POLICY_HINT = re.compile(
    r"smarter\s*proctor|smarter\s*services|proctor(?:ing|ed)?|honorlock|respondus|"
    r"lock\s*-?down|test(?:ing)?\s+polic|exam\s+polic|examity|"
    r"how\s+to\s+take\s+(?:(?:the|a)\s+)?(?:test|exam)|testing\s+instructions|"
    r"remote\s+proctor|online\s+proctor",
    re.I)
SYLLABUS_TITLE = re.compile(r"\bsyllabus\b|course\s+polic", re.I)


def policy_relevant(title: str, body: str = "") -> bool:
    return bool(POLICY_HINT.search(title or "") or POLICY_HINT.search(body or ""))


def extract_policy_passages(text: str, window: int = 900) -> str:
    """The testing / SmarterProctoring part of a long syllabus, not the start.

    A syllabus leads with outcomes and the calendar. Taking the first few
    thousand characters drops the proctoring section that actually lives
    further down.
    """
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain:
        return ""
    spans: list[tuple[int, int]] = []
    for match in POLICY_HINT.finditer(plain):
        start = max(0, match.start() - window)
        end = min(len(plain), match.end() + window)
        if start > 0:
            dot = plain.rfind(". ", start, match.start())
            if dot != -1:
                start = dot + 2
        if end < len(plain):
            dot = plain.find(". ", match.end(), end)
            if dot != -1:
                end = dot + 1
        spans.append((start, end))
    if not spans:
        return ""
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        pa, pb = merged[-1]
        if a <= pb + 40:
            merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
    return "\n\n".join(plain[a:b].strip() for a, b in merged)[:4000]


def needs_testing_policy(item: dict, extra: str = "") -> bool:
    """True when this announcement is about a test students have to sit."""
    if item.get("proctored") or item.get("exam"):
        return True
    kind = str(item.get("kind_label") or "").upper()
    if kind in ("TEST", "EXAM", "FINAL"):
        return True
    return bool(POLICY_HINT.search((item.get("name") or "") + " " + (extra or "")))


def announce_prompt(item: dict, description_text: str = "",
                    policy_text: str = "", extra: str = "") -> str:
    """Ask for a reminder about one assignment, from what Canvas knows."""
    lines = [
        "# The assignment",
        f"Course: {item.get('course_label') or item.get('course_code')}",
        f"Name: {item.get('name')}",
        f"Type: {item.get('kind_label')}",
    ]
    if item.get("points") is not None:
        lines.append(f"Points: {item['points']}")
    for key, word in (("unlock_at", "Opens"), ("due_at", "Due"),
                      ("lock_at", "Closes")):
        if item.get(key):
            lines.append(f"{word} (the instructor's local time): {item[key]}")
    if item.get("proctored"):
        lines.append("This one is proctored.")
    if item.get("submission_types"):
        lines.append("Submitted as: " + ", ".join(
            str(t).replace("_", " ") for t in item["submission_types"]))
    if not item.get("published"):
        lines.append("NOTE: this is still unpublished, so students cannot see it "
                     "yet. Write the announcement as if it will be published "
                     "before the announcement goes out.")
    if description_text.strip():
        lines += ["", "# What the assignment page says",
                  description_text.strip()[:2500],
                  "",
                  "Use this only to be accurate about what the task is. Do not "
                  "repeat it at length: the announcement is a reminder, not a "
                  "second copy of the instructions."]
    else:
        lines += ["",
                  "The assignment page has no description. Do not invent one."]
    if policy_text.strip():
        lines += ["", "# How this course says to take a test (from the syllabus)",
                  policy_text.strip()[:4000],
                  "",
                  "These are the course's own instructions, taken from the "
                  "syllabus. Name only the requirements that appear here. Do "
                  "not add steps that are not in this text."]
    elif needs_testing_policy(item, extra):
        lines += ["",
                  "You were not given this course's testing instructions. Do "
                  "not name a vendor, a webcam, an ID check, a lock-down "
                  "browser, a password, or a testing center. Say the test is "
                  "proctored if that is in the assignment name, say when it "
                  "opens and is due, and tell them to follow the instructions "
                  "on the test itself."]
    lines += ["", """# What to return

Return ONLY this JSON object:
{
  "title": "<subject line, under 60 characters, no course code>",
  "message": "<the announcement itself, plain sentences>"
}

The message is plain sentences, not HTML. The Studio wraps it in the school
look for the preview and for Canvas."""]
    return "\n".join(lines)


def looks_like_html(text: str) -> bool:
    return bool(re.match(r"(?is)\s*<(div|p|h[1-6]|ul|ol|span|table|section|article)\b",
                         text or ""))


def wrap_announcement(message: str, look: str = "hybrid", brand: dict | None = None) -> str:
    """Turn plain announcement sentences into Canvas-safe HTML in the school
    look. Already-marked-up text is cleaned, not wrapped a second time.

    The Canvas topic title is the heading students see, so this wrapper does
    not repeat it as an h2. It is a gold-bar card with the body, the same
    surface a generated page uses.
    """
    from . import htmlclean
    from .content.generate import LOOKS, load_brand

    raw = (message or "").strip()
    if not raw:
        return ""
    if looks_like_html(raw):
        return htmlclean.clean(raw, "")
    brand = brand or load_brand()
    look = look if look in LOOKS else "hybrid"
    c = brand["colors"]
    f = brand["fonts"]
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    body = []
    for para in paras:
        lines = "<br>".join(html_escape(line, quote=True) for line in para.split("\n"))
        body.append(
            f'<p style="margin: 0 0 12px; font-size: 15px; color: {c["body_text"]};">'
            f"{lines}</p>")
    inner = "".join(body)
    fill = (f"background: {c['navy']}; " if look in ("hybrid", "rich") else "")
    eyebrow = c["gold"] if look in ("hybrid", "rich") else c["navy"]
    band = (
        f'<div style="padding: 14px 20px; border-radius: 8px; {fill}'
        f'border-top: 5px solid {c["gold"]};">'
        f'<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
        f'color: {eyebrow}; font-weight: 700;">Announcement</div></div>'
    )
    card_bg = c["card_fill"] if look == "rich" else "transparent"
    return htmlclean.clean(
        f'<div style="max-width: 980px; margin: 0 auto; font-family: {f["body"]}; '
        f'line-height: 1.55; color: {c["body_text"]};">'
        f"{band}"
        f'<div style="margin-top: 14px; padding: 18px 20px; border-radius: 8px; '
        f'border: 1px solid {c["hairline"]}; background: {card_bg};">{inner}</div>'
        f"</div>",
        "",
    )
