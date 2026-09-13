"""Type a student's name; Anthropic sees a tag.

The grader already pseudonymises the work it sends for marking. The Assistant
did not, because the Assistant is free text: you type "why is Jordan Alvarez
failing" and every word of that goes to a model running on somebody else's
computer. Which meant the honest answer to "can I ask it about a student?" was
no.

This is the bridge. A course gets one stable map -- `Student-1`, `Student-2`,
in Canvas user-id order -- kept in `data/<course>/names.json` and never
uploaded anywhere. Outbound, every way of writing a student's name is swapped
for their tag. Inbound, the tags in Claude's reply are turned back into names
before the page draws them, so the conversation reads normally at both ends
and the roster stays on this machine.

What it does not do, said plainly because the UI says it too:

  * It only knows this course's roster. A name that is not on it is not a name
    as far as this module is concerned, and goes out as typed.
  * It covers what you type and what comes back. It cannot cover a file the
    model reads: if you let the Assistant open the gradebook, real names go
    with it. That is what the Allow card is for.
  * A surname two students share is refused rather than guessed. Guessing
    would either send a real name or attribute the question to the wrong
    person, and both are worse than a question.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE = "names.json"
PREFIX = "Student"
TAG = re.compile(r"\b" + PREFIX + r"-(\d{1,4})\b")

# Family and given names that are also ordinary words. For these the full name
# has to be written out: swapping a bare one would rewrite "how long is the
# essay" into "how Student-4 is the essay", which is worse than not swapping.
ALSO_WORDS = {
    "april", "art", "august", "bill", "chance", "chase", "cliff", "dean",
    "drew", "earl", "faith", "forest", "frank", "grace", "grant", "green",
    "hope", "house", "hunter", "joy", "june", "king", "lane", "long", "love",
    "mark", "may", "miles", "moore", "noble", "page", "parker", "price",
    "rich", "rose", "royal", "rusty", "sky", "small", "stone", "summer",
    "will", "wood", "young",
}
# Too short to be safely matched on their own.
MIN_TOKEN = 3


class Swap:
    """One substitution that happened, for the "3 names swapped" chip."""

    def __init__(self, wrote: str, tag: str, name: str):
        self.wrote, self.tag, self.name = wrote, tag, name

    def view(self) -> dict:
        return {"wrote": self.wrote, "tag": self.tag, "name": self.name}


class Masked:
    def __init__(self, text: str, swaps: list[Swap], ambiguous: list[dict]):
        self.text = text
        self.swaps = swaps
        self.ambiguous = ambiguous

    @property
    def clean(self) -> bool:
        return not self.ambiguous

    def sentence(self) -> str:
        """What the ambiguity is, and what to write instead."""
        if not self.ambiguous:
            return ""
        parts = []
        for row in self.ambiguous:
            who = " and ".join(row["candidates"])
            parts.append(f'"{row["wrote"]}" is {who}')
        return ("Two students match what you wrote, so nothing was sent: "
                + "; ".join(parts) + ". Write the full name and send it again.")

    def view(self) -> dict:
        return {"swapped": [s.view() for s in self.swaps],
                "ambiguous": self.ambiguous}


class NameMap:
    """One course's roster, its tags, and the substitutions both ways."""

    def __init__(self, course_id, students: list[dict] | None = None,
                 path: Path | None = None, enabled: bool = True):
        self.course_id = str(course_id)
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        self.by_tag: dict[str, dict] = {}
        self.by_user: dict[str, str] = {}
        self._needles: list[tuple[re.Pattern, str]] = []
        self._shared: dict[str, list[str]] = {}
        if students is not None:
            self.absorb(students)

    # -------------------------------------------------------------- the map
    def absorb(self, students: list[dict]) -> bool:
        """Add anyone not already tagged. Existing tags never move: a tag that
        changed meaning between two sessions would make the saved conversation
        say something that is not true. Returns whether anything was added."""
        known = {row.get("user_id") for row in self.by_tag.values()}
        highest = max((int(t.split("-")[1]) for t in self.by_tag), default=0)
        added = False
        for user in sorted(students or [], key=lambda u: _num(u.get("id"))):
            uid = str(user.get("id") or "").strip()
            if not uid or uid in known:
                continue
            highest += 1
            tag = f"{PREFIX}-{highest}"
            self.by_tag[tag] = {
                "user_id": uid,
                "name": (user.get("name") or "").strip(),
                "sortable_name": (user.get("sortable_name") or "").strip(),
                "short_name": (user.get("short_name") or "").strip(),
                "login_id": (user.get("login_id") or "").strip(),
                "sis_user_id": str(user.get("sis_user_id") or "").strip(),
                "email": (user.get("email") or "").strip(),
            }
            known.add(uid)
            added = True
        self.by_user = {row["user_id"]: tag for tag, row in self.by_tag.items()}
        self._build()
        return added

    def tag_for(self, user_id) -> str:
        return self.by_user.get(str(user_id), "")

    def name_for(self, tag: str) -> str:
        return (self.by_tag.get(tag) or {}).get("name", "")

    def __len__(self) -> int:
        return len(self.by_tag)

    # ------------------------------------------------------------- matching
    def _build(self) -> None:
        """Every spelling of every student, longest first.

        Longest first matters: "Jordan Alvarez" has to be taken before
        "Jordan", or the surname is left behind in the text.
        """
        whole: list[tuple[str, str]] = []      # (needle, tag), case-insensitive
        single: dict[str, set[str]] = {}       # token -> tags that claim it
        for tag, row in self.by_tag.items():
            # sortable_name ("Alvarez, Jordan") is a needle in its own right and
            # not only as the flipped form, or the two halves match separately
            # and the message goes out reading "Student-1, Student-1".
            for field in ("name", "sortable_name", "short_name", "login_id",
                          "sis_user_id", "email"):
                value = (row.get(field) or "").strip()
                if len(value) >= MIN_TOKEN:
                    whole.append((value, tag))
            flipped = _flip(row.get("sortable_name", ""))
            if flipped:
                whole.append((flipped, tag))
            for token in _tokens(row.get("name", "")) | _tokens(_flip(row.get("sortable_name", ""))):
                single.setdefault(token, set()).add(tag)

        self._shared = {t: sorted(tags) for t, tags in single.items() if len(tags) > 1}
        needles: list[tuple[re.Pattern, str]] = []
        seen: set[str] = set()
        for value, tag in sorted(whole, key=lambda p: -len(p[0])):
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            needles.append((re.compile(_bound(value), re.IGNORECASE), tag))
        # Bare given or family names, only where exactly one student owns them
        # and the word is not also an ordinary English word. Case-sensitive,
        # because a capital is the only evidence that "Chase" is a person.
        for token, tags in sorted(single.items(), key=lambda p: -len(p[0])):
            if len(tags) != 1 or token.lower() in ALSO_WORDS or len(token) < MIN_TOKEN:
                continue
            if token.lower() in seen:
                continue
            needles.append((re.compile(_bound(token)), next(iter(tags))))
        self._needles = needles

    def mask(self, text: str) -> Masked:
        """Real names out, tags in. Nothing is guessed."""
        if not text or not self.enabled or not self.by_tag:
            return Masked(text or "", [], [])
        out = text
        swaps: list[Swap] = []
        for pattern, tag in self._needles:
            def swap(m, tag=tag, swaps=swaps):
                swaps.append(Swap(m.group(0), tag, self.name_for(tag)))
                return tag
            out = pattern.sub(swap, out)

        # Anything left that two students could answer to. Checked against the
        # masked text, so a full name already swapped does not raise it.
        ambiguous = []
        for token, tags in self._shared.items():
            if token.lower() in ALSO_WORDS or len(token) < MIN_TOKEN:
                continue
            if re.search(_bound(token), out):
                ambiguous.append({"wrote": token,
                                  "candidates": [self.name_for(t) for t in tags]})
        return Masked(out, swaps, ambiguous)

    def unmask(self, text: str) -> str:
        """Tags back into names, for the page only. Never for anything stored,
        and never for anything on its way out."""
        if not text or not self.by_tag:
            return text or ""
        return TAG.sub(lambda m: self.name_for(m.group(0)) or m.group(0), str(text))

    def hold_len(self, text: str) -> int:
        """How much of the end of a streamed chunk might still become a tag.

        Claude's prose arrives a few characters at a time, and a tag lands
        split across events: "Stud", "ent-1 is behind". Turned back one event
        at a time, neither half matches and the page shows the tag instead of
        the name. So a chunk is cut short of anything that could still be the
        beginning of one, and the rest waits for the next chunk.
        """
        if not text or not self.by_tag:
            return 0
        stem = PREFIX + "-"
        for n in range(min(len(text), len(stem) + 4), 0, -1):
            tail = text[-n:]
            if stem.startswith(tail):
                return n
            if tail.startswith(stem) and tail[len(stem):].isdigit():
                return n
        return 0

    def unmask_event(self, ev: dict) -> dict:
        """A transcript event with every human-readable field turned back."""
        if not self.by_tag or not isinstance(ev, dict):
            return ev
        fields = [k for k in ("text", "summary", "what", "detail", "description",
                              "reason", "command") if isinstance(ev.get(k), str)]
        if not fields:
            return ev
        return dict(ev, **{k: self.unmask(ev[k]) for k in fields})

    def roster_note(self) -> str:
        """The one line the Assistant's rail shows about all this."""
        if not self.enabled:
            return ("Names are sent as you type them: pseudonymize is off in "
                    "config.json.")
        if not self.by_tag:
            return ("No roster read for this course yet, so names go out as you "
                    "type them. Open Grade once to read it.")
        return (f"{len(self.by_tag)} students on this roster are swapped for "
                f"{PREFIX}-1, {PREFIX}-2 ... before anything is sent, and turned "
                "back for you here. The list of who is who stays on this PC.")

    # ------------------------------------------------------------- on disk
    def to_json(self) -> dict:
        return {
            "note": "Tag -> student. Local only: this file is never uploaded, "
                    "not to Canvas and not to Anthropic.",
            "course_id": self.course_id,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "students": self.by_tag,
        }

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def load(self) -> bool:
        if not self.path or not self.path.is_file():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        students = raw.get("students")
        if not isinstance(students, dict):
            return False
        self.by_tag = {str(k): dict(v) for k, v in students.items()
                       if isinstance(v, dict) and v.get("user_id")}
        self.by_user = {row["user_id"]: tag for tag, row in self.by_tag.items()}
        self._build()
        return True


# --------------------------------------------------------------------- cache
_CACHE: dict[str, NameMap] = {}
_LOCK = threading.RLock()


def for_course(app, course_id, refresh: bool = False) -> NameMap:
    """The course's map: from memory, then disk, then Canvas.

    Canvas is only asked when there is nothing on disk or a refresh was asked
    for. A roster read is a read, so it never needs a confirmation -- but it is
    still a network call on the path of someone pressing Enter, and doing that
    every message would make the composer feel broken.
    """
    cid = str(course_id)
    with _LOCK:
        nm = _CACHE.get(cid)
        if nm is not None and not refresh:
            return nm
        path = Path(app.course_dir(cid)) / FILE
        enabled = bool(getattr(app.cfg, "pseudonymize", True))
        nm = nm or NameMap(cid, path=path, enabled=enabled)
        nm.enabled = enabled
        if not nm.by_tag:
            nm.load()
        if refresh or not nm.by_tag:
            try:
                added = nm.absorb(app.client.students(cid))
                if added:
                    nm.save()
            except Exception:  # noqa: BLE001
                # Offline, or a token without roster rights. An empty map masks
                # nothing, and the rail says so rather than pretending.
                pass
        _CACHE[cid] = nm
        return nm


def forget(course_id=None) -> None:
    with _LOCK:
        if course_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(course_id), None)


# -------------------------------------------------------------------- bits
def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bound(value: str) -> str:
    """A whole-word match for something that may contain spaces or dots.

    The edges stop at word characters and at `@`, and deliberately not at a
    full stop: a name at the end of a sentence is the ordinary case, and
    refusing to match "ask Jordan Alvarez." would have left the commonest way
    of writing a name untouched.
    """
    return r"(?<![\w@])" + re.escape(value).replace(r"\ ", r"\s+") + r"(?![\w@])"


def _flip(sortable: str) -> str:
    """"Alvarez, Jordan" -> "Jordan Alvarez"."""
    if "," not in (sortable or ""):
        return ""
    family, _, given = sortable.partition(",")
    given, family = given.strip(), family.strip()
    return f"{given} {family}" if given and family else ""


def _tokens(name: str) -> set[str]:
    """The parts of a name worth matching on their own: no initials, no
    particles, nothing with a dot in it."""
    out = set()
    for part in re.split(r"[\s,]+", name or ""):
        part = part.strip(".,'\"")
        if len(part) >= MIN_TOKEN and part[:1].isalpha() and "." not in part:
            out.add(part)
    return out
