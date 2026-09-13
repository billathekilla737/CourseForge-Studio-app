"""The Canvas Inbox: what students asked, what it means, and a drafted reply.

Three things happen here and they are worth keeping apart.

**Reading** is a Canvas call on the grading-scoped client, because
`/conversations` is student data and `canvas_policy` refuses it to everything
else. Nothing is marked read: somebody skimming this screen has not answered
anybody, and clearing the unread flag on their behalf would take away the only
mark they had.

**Thinking** happens with the names taken out. A student's message goes to
Claude as `Student-14 wrote`, and the body is scrubbed of names, emails, ids
and phone numbers first -- including the names of other students they mention,
which is the case a per-message swap would miss. Claude answers in tags. The
tags become names again on the way to the screen and on the way into the reply,
so what the student eventually reads says their name and what Anthropic saw
never did.

**Sending** is the instructor's, always. A draft sits in a box until somebody
presses Send on that specific reply, and that press goes through the same
confirm gate as every other Canvas write: refused once, shown as a sentence,
sent on the second pass. There is no rule engine and no unattended send. A
message to a student under somebody's own name is the least reversible thing
this tool can do, and the one place where "it seemed right at the time" is not
a defence anybody wants to make to a dean.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from . import audit, identity, llm, pseudonym

MAX_BODY = 4000          # of one message, before it is handed to a model
MAX_THREADS = 60


def _text(raw: str) -> str:
    """Canvas message bodies are plain text with the odd entity. Flatten."""
    out = html.unescape(str(raw or ""))
    out = re.sub(r"<[^>]+>", " ", out)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", out)).strip()


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    hours = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 36:
        return "%d hour%s ago" % (round(hours), "" if round(hours) == 1 else "s")
    return "%d days ago" % round(hours / 24)


def course_of(row: dict):
    """The course a thread belongs to, or None for account-level mail."""
    code = str(row.get("context_code") or "")
    return code.split("course_", 1)[1] if code.startswith("course_") else None


class Thread:
    """One conversation, and the tags for the people in it.

    Tags come from the course's own map where the thread has a course, so
    Student-14 means the same person here as in the Assistant. A thread with no
    course gets a map of its own, because a message still has to be readable
    without a name in it.
    """

    def __init__(self, app, row: dict, me_id=None):
        self.row = row
        self.id = row.get("id")
        self.course_id = course_of(row)
        self.me_id = str(me_id or "")
        self.names = (identity.for_course(app, self.course_id)
                      if self.course_id else identity.NameMap("inbox", []))
        people = [p for p in (row.get("participants") or [])
                  if str(p.get("id")) != self.me_id]
        # Canvas Inbox threads are usually account-level rather than
        # course-level, so the first question is whether this person is already
        # tagged in some course on this machine. If they are, they keep that
        # number: Student-2 has to mean the same person here as in the
        # Assistant, or the tag is just a different name for them.
        for p in people:
            if self.names.tag_for(p.get("id")):
                continue
            hit = identity.known(app, p.get("id"))
            if hit:
                self.names.adopt(hit[0], hit[1])
        missing = [p for p in people if not self.names.tag_for(p.get("id"))]
        if missing:
            self.names.absorb(missing)
        self.people = people

    def tag(self, user_id) -> str:
        return self.names.tag_for(user_id) or "Someone"

    def mask(self, text: str) -> str:
        """Names out of a message body, whoever they belong to.

        Every roster name, not only the sender's: a student writing "Jordan
        said the deadline moved" hands over somebody else's name, and a swap
        that only knew the sender would let it through.
        """
        masked = self.names.mask(_text(text)[:MAX_BODY])
        return pseudonym.Pseudonymizer([], enabled=False).scrub(masked.text)

    def unmask(self, text: str) -> str:
        return self.names.unmask(text or "")

    def view(self) -> dict:
        """The thread as the screen draws it: real names, this machine only."""
        last = self.row.get("last_message") or ""
        return {
            "id": self.id,
            "course_id": self.course_id,
            "subject": self.row.get("subject") or "(no subject)",
            "unread": self.row.get("workflow_state") == "unread",
            "messages": self.row.get("message_count") or 1,
            "at": self.row.get("last_message_at"),
            "ago": _when(self.row.get("last_message_at")),
            "preview": _text(last)[:180],
            "with": [{"tag": self.tag(p.get("id")), "name": p.get("name") or "",
                      "user_id": str(p.get("id"))} for p in self.people],
        }

    def transcript(self, full: dict, mask: bool = True) -> list[dict]:
        """Every message in the thread, oldest first.

        `mask` is for the model, not for the screen. The instructor is reading
        their own inbox: if a student wrote their phone number, showing them
        "[PHONE]" is the tool withholding the very thing the student sent. So
        the screen gets the message as written, and only the copy that leaves
        the machine has anything taken out of it.
        """
        out = []
        for msg in reversed(full.get("messages") or []):
            author = str(msg.get("author_id") or "")
            body = msg.get("body") or ""
            out.append({
                "id": msg.get("id"),
                "from": "you" if author == self.me_id else self.tag(author),
                "at": msg.get("created_at"),
                "body": self.mask(body) if mask else _text(body),
            })
        return out


# ------------------------------------------------------------------ reading
def unread_count(app) -> dict:
    """What the header chip shows. One Canvas call, and it may fail quietly:
    a number in the corner is never worth a banner across the page."""
    try:
        rows = app.client.conversations(scope="unread", limit=MAX_THREADS)
        return {"unread": len(rows), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except Exception as exc:  # noqa: BLE001
        return {"unread": None, "detail": "%s: %s" % (type(exc).__name__, exc)}


def listing(app, scope: str = "", course_id=None, limit: int = 40) -> dict:
    rows = app.client.conversations(scope=scope, course_id=course_id, limit=limit)
    me = app.me_id
    threads = [Thread(app, row, me).view() for row in rows]
    return {
        "scope": scope or "inbox",
        "course_id": str(course_id) if course_id else None,
        "threads": threads,
        "unread": sum(1 for t in threads if t["unread"]),
        "note": "Read only. Nothing here is marked read, and no reply is sent "
                "until you press Send on it.",
    }


def thread(app, conversation_id) -> dict:
    row = app.client.conversation(conversation_id, mark_read=False)
    t = Thread(app, row, app.me_id)
    out = t.view()
    out["transcript"] = t.transcript(row, mask=False)
    return out


# ----------------------------------------------------------------- thinking
SYSTEM = """You are helping a college instructor answer their Canvas Inbox.

Students reach you as tags: Student-14, Student-3. You never see a real name
and must never invent one; write the tag if you need to refer to someone.

Answer with JSON and nothing else:
{"asking": "...", "kind": "...", "urgency": "routine|soon|today",
 "needs_you": true|false, "why": "...", "reply": "..."}

- "asking" is one sentence: what this student actually wants.
- "kind" is a short label: deadline, grade question, rubric, technical, absence,
  accommodation, thanks, other.
- "needs_you" is true when answering needs a judgement only the instructor can
  make -- anything about a grade, an accommodation, a late penalty, a personal
  circumstance, or anything you would be guessing at.
- "reply" is a draft the instructor can send as written: their voice, two or
  three sentences, no greeting beyond the student's tag, no sign-off, no
  promises about grades or extensions, and no invented facts about the course.
  If you do not know something, say what you would need rather than filling it
  in. When needs_you is true, draft the part you can and leave the decision to
  them in plain words.

When the instructor has told you how to answer, that instruction is the answer
and your reading of the message is not. Write what they asked for, in their
voice, even where you would have said something else; if what they want is
already decided, "needs_you" is false. If their instruction cannot be squared
with what the student actually asked, follow the instruction and say what the
mismatch is in "why" rather than quietly splitting the difference."""


def read_thread(app, conversation_id, model: str | None = None,
                instructions: str = "") -> dict:
    """What the student is asking, and a reply for the instructor to consider.

    `instructions` is the instructor saying how to answer -- "no extensions
    this time", "point them at the rubric", "warm, we have been through this".
    Empty is the ordinary case and means: work it out from the message.

    It is masked like everything else. Somebody typing "tell Jordan he can have
    until Friday" has put a real name in a prompt, and the box being theirs
    rather than the student's makes no difference to where it would end up.

    The model never sees a name either way. It sees the thread with tags in it,
    and what comes back is turned into names only on the way to the screen.
    """
    row = app.client.conversation(conversation_id, mark_read=False)
    t = Thread(app, row, app.me_id)
    msgs = t.transcript(row, mask=True)
    if not msgs:
        raise ValueError("that conversation has no messages in it")

    course = ""
    if t.course_id:
        for c in (app.store.courses() or []):
            if str(c.get("id")) == str(t.course_id):
                course = c.get("title") or c.get("name") or ""
                break
    lines = ["Course: %s" % (course or "not a course message"),
             "Subject: %s" % t.mask(t.row.get("subject") or "(no subject)"), ""]
    for m in msgs:
        lines.append("%s wrote:\n%s\n" % (m["from"], m["body"]))
    told = t.mask(instructions or "")
    if told:
        lines.append("The instructor says to answer like this:\n%s\n" % told)

    result = llm.run("\n".join(lines), model=model or getattr(app.cfg, "describe_model", "sonnet"),
                     system=SYSTEM, expect_json=True, timeout_s=180)
    data = llm.parse_json(result.text) or {}
    if not isinstance(data, dict):
        raise ValueError("the model did not answer in the shape this screen reads")

    draft = str(data.get("reply") or "").strip()
    return {
        "id": t.id,
        "asking": t.unmask(str(data.get("asking") or "").strip()),
        "kind": str(data.get("kind") or "other").strip(),
        "urgency": str(data.get("urgency") or "routine").strip(),
        "needs_you": bool(data.get("needs_you")),
        "why": t.unmask(str(data.get("why") or "").strip()),
        # Both forms: the tagged one is what was actually generated, and the
        # readable one is what goes in the box. Sending re-resolves from the
        # box, so an edit is never lost to the tag round trip.
        "draft": t.unmask(draft),
        "draft_tagged": draft,
        "instructions": instructions or "",
        "with": out_people(t),
        "note": "Nothing has been sent. This draft exists only on this computer.",
    }


def out_people(t: "Thread") -> list[dict]:
    return [{"tag": t.tag(p.get("id")), "name": p.get("name") or "",
             "user_id": str(p.get("id"))} for p in t.people]


# ------------------------------------------------------------------ sending
def send_reply(app, conversation_id, body: str, confirm_token: str | None = None,
               log=lambda *_a, **_k: None) -> dict:
    """Send one reply, to one thread, after the gate has been through twice.

    No batch form and no rule that sends on its own. Whatever a queue of
    unattended replies would save, it is not worth the morning somebody finds
    out their tool told a student something it had no business saying.
    """
    body = (body or "").strip()
    if not body:
        raise ValueError("There is nothing in the reply box to send.")
    row = app.client.conversation(conversation_id, mark_read=False)
    t = Thread(app, row, app.me_id)
    who = ", ".join(p["name"] or p["tag"] for p in out_people(t)) or "this thread"
    subject = t.row.get("subject") or "(no subject)"

    # A tag left in the text would reach the student as "Student-14". Resolve
    # from the box, so an edit the instructor made is what goes out.
    final = t.unmask(body)

    app._gate("inbox-reply",
              {"conversation_id": str(conversation_id), "body": final},
              'Send this reply to %s in Canvas about "%s"' % (who, subject),
              confirm_token,
              detail=final[:1200],
              what="replying to a student")

    log("sending the reply")
    out = app.client.reply_to_conversation(conversation_id, final)
    audit.record(
        app.course_dir(t.course_id) if t.course_id else app.store.root,
        "inbox", "replied",
        'Replied in Canvas to %s about "%s".' % (who, subject),
        students=[audit.person(p["user_id"], p["name"]) for p in out_people(t)],
        count=1, course_id=t.course_id or "",
        detail={"conversation_id": str(conversation_id), "body": final[:2000]})
    log("sent")
    return {"ok": True, "conversation_id": str(conversation_id),
            "sent_to": [p["name"] or p["tag"] for p in out_people(t)],
            "body": final, "canvas": out}


# ------------------------------------------------------------- several at once
# Marking, archiving and deleting are not sending. They change your own copy of
# a thread and the student is never told, which is why a bulk form is offered
# here and deliberately is not offered for a reply. Deleting is still the one
# that cannot be taken back, so it says so and it is its own red button.
MAX_BULK = 200

BULK_ACTIONS = {
    "read": {
        "state": "read",
        "doing": "Marking read",
        "sentence": "Mark %s read in your Canvas Inbox.",
        "note": "This changes the unread mark on your own copy. Nothing is sent, "
                "the student is not told, and you can mark them unread again.",
        "done": "marked read",
    },
    "unread": {
        "state": "unread",
        "doing": "Marking unread",
        "sentence": "Mark %s unread in your Canvas Inbox.",
        "note": "This changes the unread mark on your own copy. Nothing is sent, "
                "the student is not told, and you can mark them read again.",
        "done": "marked unread",
    },
    "archive": {
        "state": "archived",
        "doing": "Archiving",
        "sentence": "Archive %s in your Canvas Inbox.",
        "note": "Archived threads leave the inbox and stay in Canvas under "
                "Archived. Nothing is sent and the student is not told. You can "
                "move them back.",
        "done": "archived",
    },
    "unarchive": {
        "state": "read",
        "doing": "Moving to the inbox",
        "sentence": "Move %s back into your Canvas Inbox.",
        "note": "They return to the inbox as read. Nothing is sent and the "
                "student is not told.",
        "done": "moved back to the inbox",
    },
    "delete": {
        "state": None,
        "doing": "Deleting",
        "sentence": "Delete %s from your Canvas Inbox.",
        "note": "This deletes your copy only, and it cannot be undone. The "
                "student keeps their copy of the conversation and is not told. "
                "Nothing is sent.",
        "done": "deleted",
    },
}


def _bulk_ids(conversation_ids) -> list[str]:
    seen, out = set(), []
    for raw in (conversation_ids or []):
        one = str(raw).strip()
        if one and one not in seen:
            seen.add(one)
            out.append(one)
    if not out:
        raise ValueError("Pick at least one thread first.")
    if len(out) > MAX_BULK:
        raise ValueError("That is %d threads at once. %d is the most this will do "
                         "in one go, so a slip of the hand cannot empty an inbox."
                         % (len(out), MAX_BULK))
    return out


def _bulk_names(app, ids: list[str], scope: str = "") -> dict:
    """Subject and who, for the threads being acted on.

    One listing read rather than one read per thread: the confirmation has to
    name what it is about to change, and the record has to name whose messages
    they were, but neither is worth N round trips to Canvas. A thread the
    listing does not carry is named by its id and still acted on.
    """
    found: dict[str, dict] = {}
    try:
        rows = app.client.conversations(scope=scope or "", limit=MAX_THREADS)
    except Exception:  # noqa: BLE001
        return found
    wanted = set(ids)
    for row in rows:
        key = str(row.get("id"))
        if key not in wanted:
            continue
        t = Thread(app, row, app.me_id)
        found[key] = {
            "subject": row.get("subject") or "(no subject)",
            "people": out_people(t),
            "course_id": t.course_id or "",
            "was": "unread" if row.get("workflow_state") == "unread" else "read",
        }
    return found


def _bulk_line(one: str, known: dict | None, spec: dict) -> dict:
    """One row of the confirmation: which thread, and what happens to it."""
    known = known or {}
    who = ", ".join(p["name"] or p["tag"] for p in known.get("people") or [])
    subject = known.get("subject") or ("thread " + one)
    return {"label": subject + (" \u00b7 " + who if who else ""),
            "from": known.get("was") or "in your inbox",
            "to": spec["done"]}


def bulk(app, conversation_ids, action: str, scope: str = "",
         confirm_token: str | None = None, log=lambda *_a, **_k: None) -> dict:
    """Mark, archive or delete several threads at once, after the gate.

    One Canvas call per thread. The batch endpoint would be one call, but it
    answers with a progress object and no per-thread result, and a screen that
    says "12 deleted" has to be able to say which twelve and which one failed.
    """
    spec = BULK_ACTIONS.get(action)
    if spec is None:
        raise ValueError("Unknown inbox action %r. It does one of: %s."
                         % (action, ", ".join(sorted(BULK_ACTIONS))))
    ids = _bulk_ids(conversation_ids)
    known = _bulk_names(app, ids, scope)

    count = "%d thread%s" % (len(ids), "" if len(ids) == 1 else "s")
    app._gate("inbox-bulk",
              {"action": action, "conversation_ids": sorted(ids)},
              spec["sentence"] % count + " " + spec["note"],
              confirm_token,
              # The sender names the row; the state is what changes. The
              # dialog strikes the "from" value through, so a name there read
              # as though the student were the thing being deleted.
              detail=json.dumps([_bulk_line(i, known.get(i), spec) for i in ids]),
              what=spec["doing"].lower() + " conversations")

    done, failed = [], []
    for index, one in enumerate(ids, start=1):
        log("%s %d/%d" % (spec["doing"], index, len(ids)), index - 1, len(ids))
        try:
            if spec["state"] is None:
                app.client.delete_conversation(one)
            else:
                app.client.set_conversation_state(one, spec["state"])
            done.append(one)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": one, "error": "%s: %s" % (type(exc).__name__, exc)})

    # One line in the record per run, naming everyone whose messages moved.
    # Deleting somebody's message is exactly the kind of thing that has to be
    # answerable a year later.
    people, seen = [], set()
    for one in done:
        for p in (known.get(one) or {}).get("people") or []:
            if p["user_id"] not in seen:
                seen.add(p["user_id"])
                people.append(audit.person(p["user_id"], p["name"]))
    courses = {(known.get(one) or {}).get("course_id") for one in done}
    courses.discard("")
    if done:
        audit.record(
            app.course_dir(next(iter(courses))) if len(courses) == 1 else app.store.root,
            "inbox", action,
            "%s %d conversation%s in your own Canvas Inbox. Nothing was sent."
            % (spec["done"].capitalize(), len(done), "" if len(done) == 1 else "s"),
            students=people, count=len(done),
            course_id=next(iter(courses)) if len(courses) == 1 else "",
            result="failed" if failed and not done else "ok",
            detail={"conversation_ids": done,
                    "subjects": [(known.get(one) or {}).get("subject") or one
                                 for one in done],
                    "failed": failed})
    log("done", len(ids), len(ids))
    sentence = "%d thread%s %s." % (len(done), "" if len(done) == 1 else "s",
                                    spec["done"])
    if failed:
        sentence += " %d could not be changed." % len(failed)
    return {"action": action, "done": done, "failed": failed,
            "sentence_done": sentence + " Nothing was sent to anybody."}
