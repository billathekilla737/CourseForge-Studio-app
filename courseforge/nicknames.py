"""Instructor nicknames. A display spelling, not a second legal name.

John Doe with the nickname Jack is shown as John "Jack" Doe. Canvas, the
grade file, the late-policy match on the legal name, names.json, map.json,
and anything sent to a model keep "John Doe". The nickname is another way
to find the student on your screens, and another spelling that is swapped
for a tag before a message leaves this machine.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE_NAME = "nicknames.json"
SYNC_KEY = "nicknames.json"
MAX_LEN = 40

_book: "Book | None" = None
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(raw) -> str:
    """The nickname to store, or empty when the instructor is clearing it.

    Raises ValueError when the text is not a name. Empty is a clear, not
    an error.
    """
    text = str(raw or "").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    text = text.strip()
    while len(text) >= 2 and text[0] in "'“" and text[-1] in "'”":
        text = text[1:-1].strip()
    text = " ".join(text.split())
    if not text:
        return ""
    if len(text) > MAX_LEN:
        raise ValueError("Keep a nickname to 40 characters or fewer.")
    if not text[0].isalpha():
        raise ValueError("A nickname has to start with a letter.")
    for ch in text:
        if ch.isalpha() or ch in " .'-":
            continue
        raise ValueError(
            "Use letters, spaces, hyphens and apostrophes in a nickname.")
    return text


def format_name(legal: str, nickname: str) -> str:
    """First "Nick" Last. The legal spelling comes back unchanged when there
    is no nickname, or when that spelling is already in the name."""
    legal = " ".join(str(legal or "").split())
    try:
        nick = clean(nickname)
    except ValueError:
        nick = ""
    if not nick:
        return legal
    quoted = f'"{nick}"'
    if quoted in legal:
        return legal
    if not legal:
        return quoted
    if "," in legal:
        last, _, rest = legal.partition(",")
        rest_parts = rest.split()
        last = last.strip()
        if rest_parts and last:
            return " ".join([rest_parts[0], quoted, *rest_parts[1:], last])
    parts = legal.split(" ")
    return " ".join([parts[0], quoted, *parts[1:]])


def shown(legal: str, user_id=None) -> str:
    """How an instructor-facing label should read. Not for a model prompt."""
    return format_name(legal, lookup(user_id) if user_id else "")


def redact(text: str, pseud) -> str:
    """Nickname spellings in instructor text become that student's tag.

    Legal names are the pseudonymizer's job. This only covers the extra
    spelling, and only when pseudonyms are on. A nickname that is also an
    ordinary word is left, the same as a legal name that is.
    """
    if not text or pseud is None or not getattr(pseud, "enabled", False):
        return text or ""
    try:
        from .identity import ALSO_WORDS
    except Exception:  # noqa: BLE001
        ALSO_WORDS = set()
    swaps: list[tuple[str, str]] = []
    for tag, row in (getattr(pseud, "identities", None) or {}).items():
        if not isinstance(row, dict):
            continue
        uid = row.get("user_id")
        legal = " ".join(str(row.get("name") or "").split())
        nick = lookup(uid)
        if not nick:
            continue
        shown_form = format_name(legal, nick)
        if shown_form and shown_form.lower() != legal.lower():
            swaps.append((shown_form, tag))
        parts = legal.split(" ")
        last = parts[-1] if len(parts) > 1 else ""
        if last and last.lower() != nick.lower():
            swaps.append((f"{nick} {last}", tag))
        if len(nick) >= 4 and nick.lower() not in ALSO_WORDS:
            swaps.append((nick, tag))
    seen: set[str] = set()
    for spelling, tag in sorted(swaps, key=lambda pair: -len(pair[0])):
        key = spelling.lower()
        if key in seen:
            continue
        seen.add(key)
        text = re.sub(
            r"(?<!\w)" + re.escape(spelling) + r"(?!\w)",
            tag, text, flags=re.IGNORECASE)
    return text


class Book:
    """data/nicknames.json. Keyed by Canvas user id."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / FILE_NAME
        self._cache: dict | None = None
        self._mtime: float | None = None

    def read(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if self._cache is not None and mtime == self._mtime:
            return self._cache
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}
            self._mtime = mtime
            return {}
        rows = raw.get("by_id") if isinstance(raw, dict) else None
        if not isinstance(rows, dict):
            return {}
        out = {}
        for uid, row in rows.items():
            key = str(uid).strip()
            if not key:
                continue
            if isinstance(row, str):
                out[key] = {"nickname": row, "updated": ""}
            elif isinstance(row, dict):
                out[key] = {
                    "nickname": str(row.get("nickname") or ""),
                    "updated": str(row.get("updated") or ""),
                }
        self._cache = out
        self._mtime = mtime
        return out

    def write(self, by_id: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"by_id": by_id}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)
        self._cache = by_id
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None

    def get(self, user_id) -> str:
        row = self.read().get(str(user_id or "").strip()) or {}
        nick = row.get("nickname") or ""
        try:
            return clean(nick)
        except ValueError:
            return ""


def bind(root) -> None:
    """Point lookup at this data directory. None clears it (tests)."""
    global _book
    with _lock:
        _book = Book(root) if root else None


def current() -> Book | None:
    return _book


def lookup(user_id, root=None) -> str:
    book = Book(root) if root else _book
    if book is None or not user_id:
        return ""
    return book.get(user_id)


def plain(by_id: dict) -> dict:
    """User id to nickname, skipping clears."""
    out = {}
    for uid, row in (by_id or {}).items():
        nick = row.get("nickname") if isinstance(row, dict) else row
        try:
            nick = clean(nick)
        except ValueError:
            nick = ""
        if nick:
            out[str(uid)] = nick
    return out


def merge_maps(left: dict, right: dict) -> dict:
    """Per student, the copy with the later timestamp. A tie keeps left."""
    out = {}
    for uid in set(left or {}) | set(right or {}):
        a = (left or {}).get(uid)
        b = (right or {}).get(uid)
        if a is None:
            chosen = b
        elif b is None:
            chosen = a
        else:
            au = str((a or {}).get("updated") or "") if isinstance(a, dict) else ""
            bu = str((b or {}).get("updated") or "") if isinstance(b, dict) else ""
            chosen = a if au >= bu else b
        if isinstance(chosen, str):
            chosen = {"nickname": chosen, "updated": ""}
        if not isinstance(chosen, dict):
            continue
        out[str(uid)] = {
            "nickname": str(chosen.get("nickname") or ""),
            "updated": str(chosen.get("updated") or ""),
        }
    return out


def _remote_map(app) -> dict:
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "get"):
        return {}
    try:
        env = sync.get(SYNC_KEY) or {}
    except Exception:  # noqa: BLE001
        return {}
    payload = env.get("payload") if isinstance(env, dict) else None
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("by_id") if isinstance(payload.get("by_id"), dict) else {}
    return merge_maps(rows, {})


def _push(app, by_id: dict) -> dict:
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        return {"did": "local"}
    payload = {"by_id": by_id}
    try:
        return sync.put(SYNC_KEY, payload)
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name != "Conflict" and not name.endswith("Conflict"):
            return {"did": "error", "error": str(exc)[:200]}
    try:
        return sync.put(SYNC_KEY, payload, force=True)
    except Exception as exc:  # noqa: BLE001
        return {"did": "error", "error": str(exc)[:200]}


def reconcile(app) -> dict:
    """Local file and the Canvas copy, merged. Pushes when this side is ahead."""
    root = Path(app.store.root)
    bind(root)
    book = Book(root)
    local = book.read()
    remote = _remote_map(app)
    merged = merge_maps(local, remote)
    if merged != local:
        book.write(merged)
        try:
            from . import identity
            identity.rebuild_all()
        except Exception:  # noqa: BLE001
            pass
    if merged != remote and (merged or remote):
        _push(app, merged)
    return merged


def save(app, user_id: str, nickname) -> dict:
    """Set or clear one student's nickname, then copy the book to Canvas."""
    uid = str(user_id).strip()
    nick = clean(nickname)
    root = Path(app.store.root)
    bind(root)
    book = Book(root)
    merged = merge_maps(book.read(), _remote_map(app))
    merged[uid] = {"nickname": nick, "updated": _now()}
    book.write(merged)
    pushed = _push(app, merged)
    try:
        from . import identity
        identity.rebuild_all()
    except Exception:  # noqa: BLE001
        pass
    return {"user_id": uid, "nickname": nick, "sync": pushed}
