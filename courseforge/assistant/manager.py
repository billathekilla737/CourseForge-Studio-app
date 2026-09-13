"""The Assistant's registry: one conversation per course, one table of
questions waiting for a click.

`Manager` hangs off the App as `app.assistant`. For each course it keeps a
`Course` record: the live `Session` (if any), a bounded ring of transcript
events with sequence numbers the page polls with `events?since=`, the busy
flag for the current turn, and the conversation log on disk. Permission
requests from the hook land in `pending`, each with a `threading.Event` the
HTTP handler waits on until `answer()` sets the decision or the timer denies.

Nothing here reaches Canvas. The verbs the session runs do that, each under
its own Allow click.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .. import claude_cli, identity, ledger
from . import gate, session as S

RING = 4000                 # transcript events kept in memory per course
CONVERSATION_TAIL = 12_000  # bytes of conversation.txt shown after a restart
# The server denies a little before the hook's own timer so a stale Allow can
# never land on a call that has already failed.
GRACE_S = 10.0


class NameProblem(ValueError):
    """A message stopped because of a student's name, with what to do about it.

    A ValueError so every existing caller still turns it into a 409, and a
    class of its own so the route can hand the page the choices rather than
    only a sentence.
    """

    def __init__(self, masked):
        super().__init__(masked.sentence())
        self.masked = masked

    def view(self) -> dict:
        out = self.masked.view()
        out["error"] = str(self)
        # Only a near miss may be overridden. An ambiguous surname has no safe
        # answer here, so the page is not offered one.
        out["can_send_anyway"] = bool(self.masked.near and not self.masked.ambiguous)
        return out


class Pending:
    def __init__(self, request_id: str, course_id: str, req: dict, timeout_s: float):
        self.id = request_id
        self.course_id = course_id
        self.req = req
        self.event = threading.Event()
        self.decision: str | None = None
        self.reason = ""
        self.auto = False
        self.created = time.time()
        self.expires_at = self.created + timeout_s

    def view(self) -> dict:
        """What the page sees. The tool input is trimmed to what the card
        shows: the command or path, and the model's own description (muted).
        Nothing else in the input is needed, and a Write's `content` could be
        anything."""
        ti = self.req.get("tool_input") or {}
        command = (ti.get("command") or ti.get("file_path") or ti.get("notebook_path")
                   or ti.get("url") or ti.get("path") or "")
        if not command:
            try:
                command = json.dumps(ti, indent=1, sort_keys=True)[:2000]
            except Exception:  # noqa: BLE001
                command = ""
        return {"request_id": self.id, "course_id": self.course_id,
                "tool_name": self.req.get("tool_name"), "kind": self.req.get("kind"),
                "what": self.req.get("what") or self.req.get("summary") or "",
                "summary": self.req.get("summary") or "",
                "description": (ti.get("description") or "").strip(),
                "why": self.req.get("why") or "", "command": str(command)[:4000],
                "created": self.created, "expires_at": self.expires_at,
                "tool_use_id": self.req.get("tool_use_id"),
                # The name swap covers what is typed and what comes back. It
                # cannot cover a file: whatever this reads goes to Anthropic as
                # it is. Say so on the card that decides it, not in a document.
                "leaks_names": self.req.get("kind") in ("student-data", "read")}


class Course:
    """Everything the Assistant keeps for one course while the server runs."""

    def __init__(self, course_id: str, course_dir: Path):
        self.id = str(course_id)
        self.dir = Path(course_dir)
        self.assistant_dir = self.dir / "assistant"
        self.workspace = self.dir / "workspace"
        self.session: S.Session | None = None
        self.ring: deque = deque(maxlen=RING)
        self.seq = 0
        self.busy = False
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self._text_buf: list[str] = []
        # What is held back out of a streamed chunk because it might still turn
        # into a Student-N tag. See NameMap.hold_len.
        self.mask_tail = ""
        self.model = ""
        self.last_error = ""

    # ---- events
    def push(self, ev: dict) -> dict:
        with self.lock:
            self.seq += 1
            ev = dict(ev)
            ev["seq"] = self.seq
            ev["at"] = time.time()
            self.ring.append(ev)
            self._log(ev)
            self.changed.notify_all()
        return ev

    def since(self, seq: int) -> tuple[list[dict], bool]:
        """Events after `seq`, and whether the ring has forgotten some of the
        gap (the page then rebuilds from the start)."""
        with self.lock:
            oldest = self.ring[0]["seq"] if self.ring else self.seq + 1
            gap = seq > 0 and seq + 1 < oldest
            return [e for e in self.ring if e["seq"] > seq], gap

    # ---- the conversation log: what the page showed, never tool output
    def _log(self, ev: dict) -> None:
        k = ev.get("kind")
        if k == "text":
            self._text_buf.append(ev.get("text") or "")
            return
        if k == "text_end":
            text = "".join(self._text_buf).strip()
            self._text_buf = []
            if text:
                self._append("\n%s\n" % text)
            return
        if k == "user":
            self._append("\n[You] %s\n" % (ev.get("text") or "").strip())
        elif k == "tool":
            mark = "  > " if ev.get("decision") == "allow" else "  > (asking you) "
            self._append(mark + (ev.get("summary") or ev.get("name") or "") + "\n")
        elif k == "permission_answered":
            if ev.get("auto"):
                mark = "  [No answer, treated as Deny]"
            else:
                mark = "  [Allowed]" if ev.get("decision") == "allow" else "  [Denied]"
            self._append("%s %s\n" % (mark, ev.get("what") or ""))
        elif k == "notice":
            self._append("  ! %s\n" % (ev.get("text") or ""))
        elif k == "result" and ev.get("error"):
            self._append("  ! Claude stopped with an error.\n")

    def _append(self, text: str) -> None:
        try:
            self.assistant_dir.mkdir(parents=True, exist_ok=True)
            with open(self.assistant_dir / "conversation.txt", "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def history_tail(self) -> str:
        path = self.assistant_dir / "conversation.txt"
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > CONVERSATION_TAIL:
                    fh.seek(size - CONVERSATION_TAIL)
                    fh.readline()           # drop the partial line
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def rotate_conversation(self) -> None:
        path = self.assistant_dir / "conversation.txt"
        if path.is_file():
            try:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.move(str(path), str(self.assistant_dir / ("conversation-%s.txt" % stamp)))
            except OSError:
                pass


class Manager:
    """`app.assistant`. Thread-safe; every route calls straight into it."""

    HEADLINES = {
        "canvas-write": "Claude wants to change the live Canvas course",
        "system": "Claude wants to change something on this PC",
        "local-change": "Claude wants to change a file outside the workspace folder",
        "local-script": "Claude wants to write a script or settings file",
        "egress": "Claude wants to contact a website other than Canvas",
        "run": "Claude wants to run a command the Studio cannot vouch for",
        "read": "Claude wants to read a file outside the workspace folder",
        "student-data": "Claude wants to touch grading data",
        "unknown": "Claude wants to use a tool the Studio does not know",
    }

    def __init__(self, app, port: int | None = None):
        self.app = app
        self.cfg = app.cfg
        self.secret = uuid.uuid4().hex
        self._port = port
        self.courses: dict[str, Course] = {}
        self.pending: dict[str, Pending] = {}
        self.lock = threading.RLock()
        self._skill_dir: Path | None = None
        self._gate_checked: str | None = None     # None = not yet; "" = fine; else the problem

    # ---- plumbing
    @property
    def port(self) -> int:
        return int(self._port or self.cfg.port)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "assistant_enabled", True))

    def ask_timeout(self) -> float:
        return float(getattr(self.cfg, "assistant_ask_timeout_s", 1200) or 1200)

    def course(self, course_id) -> Course:
        cid = str(course_id)
        with self.lock:
            c = self.courses.get(cid)
            if c is None:
                c = Course(cid, self.app.course_dir(cid))
                self.courses[cid] = c
            return c

    def names(self, course_id, refresh: bool = False):
        """This course's real-name-to-tag map.

        Never raises: a map with nobody in it swaps nothing, and the rail says
        so rather than letting the composer look broken because Canvas was
        slow.
        """
        try:
            return identity.for_course(self.app, course_id, refresh=refresh)
        except Exception:  # noqa: BLE001
            return identity.NameMap(course_id, [], enabled=False)

    def course_name(self, course_id) -> str:
        """From the picker's cache only; never a Canvas call on this path."""
        cid = str(course_id)
        try:
            for c in self.app.store.courses() or []:
                if str(c.get("id")) == cid:
                    return c.get("name") or "Course %s" % cid
        except Exception:  # noqa: BLE001
            pass
        return "Course %s" % cid

    def skill_dir(self, say: Callable[[str], None] | None = None) -> Path:
        if self._skill_dir is None:
            self._skill_dir = S.ensure_skill_installed(say or (lambda *_: None))
        return self._skill_dir

    def check_gate(self) -> str | None:
        """The canary, once per server run. A failing gate means no session."""
        if self._gate_checked is None:
            self._gate_checked = S.verify_hook_gate() or ""
        return self._gate_checked or None

    # ---- state for the page
    def state(self, course_id) -> dict:
        c = self.course(course_id)
        st = S.load_session_state(c.assistant_dir) or {}
        with c.lock:
            alive = bool(c.session and c.session.alive())
            out = {
                "course_id": c.id, "course_name": self.course_name(c.id),
                "enabled": self.enabled,
                "alive": alive, "busy": c.busy and alive, "seq": c.seq,
                "session_id": (c.session.session_id if c.session else st.get("session_id")),
                "started": st.get("started"), "last_used": st.get("last_used"),
                "has_conversation": bool(st),
                "model": c.model or getattr(self.cfg, "assistant_model", "") or "Claude Code default",
                "workspace": str(c.workspace),
                "quick_jobs": S.QUICK_JOBS,
                "headlines": self.HEADLINES,
                "pending": self.pending_for(c.id),
                "claude": bool(shutil.which("claude")),
                "last_error": c.last_error,
            }
            names = self.names(c.id)
            out["names"] = {"enabled": bool(names.enabled and len(names)),
                            "students": len(names), "note": names.roster_note()}
            # After a server restart the ring is empty; the page shows the log
            # tail as "earlier" so the conversation does not look lost.
            out["history"] = names.unmask(c.history_tail()) if not c.ring else ""
        return out

    def events(self, course_id, since: int) -> dict:
        c = self.course(course_id)
        evs, gap = c.since(int(since or 0))
        # Tags become names on the way to the page and nowhere else. What is in
        # the ring, in conversation.txt and in events.jsonl stays as it was
        # sent, so the record says what actually left this machine.
        names = self.names(c.id)
        evs = [names.unmask_event(e) for e in evs]
        with c.lock:
            alive = bool(c.session and c.session.alive())
            return {"events": evs, "seq": c.seq, "gap": gap,
                    "pending": self.pending_for(c.id),
                    "busy": c.busy and alive, "alive": alive}

    def pending_for(self, course_id) -> list[dict]:
        cid = str(course_id)
        with self.lock:
            return [p.view() for p in self.pending.values() if p.course_id == cid]

    # ---- the conversation
    def send(self, course_id, text: str, model: str | None = None,
             allow_near: bool = False) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Type something to send first.")
        if not self.enabled:
            raise PermissionError("The Assistant is turned off in config.json (assistant_enabled).")
        c = self.course(course_id)

        # Real names are swapped for this course's tags before anything is
        # sent, and this is the only place a message enters the session, so
        # there is no second path that skips it.
        #
        # Two things stop the message rather than guessing. A name two students
        # share: sending it leaks a real surname, and picking one answers about
        # the wrong person. And a name spelled almost right: it matches nothing,
        # so it would go out as typed while the person believed it was swapped.
        # The second one can be overridden, because it is the one that can be
        # wrong about an ordinary word; the first cannot, because there is no
        # safe way to resolve it here.
        names = self.names(course_id)
        masked = names.mask(text, allow_near=allow_near)
        if masked.ambiguous or masked.near:
            raise NameProblem(masked)
        outgoing = masked.text

        with c.lock:
            if c.busy and c.session and c.session.alive():
                raise ValueError("Claude is still working on the last message. "
                                 "Stop it first, or wait for it to finish.")
            if not (c.session and c.session.alive()):
                self._start(c, model)
            c.busy = True
            # The ring and the log keep what was sent, not what was typed.
            c.push({"kind": "user", "text": outgoing})
            try:
                c.session.send(outgoing)
            except Exception as exc:  # noqa: BLE001
                c.busy = False
                raise RuntimeError("Could not hand the message to Claude Code: %s" % exc)
            S.save_session_state(c.assistant_dir, c.session.session_id,
                                 started=(S.load_session_state(c.assistant_dir) or {}).get("started"))
        return {"ok": True, "seq": c.seq, "session_id": c.session.session_id,
                **masked.view()}

    def _start(self, c: Course, model: str | None = None) -> None:
        """Start (or resume) the course's session. Refuses when the gate does
        not prove itself: a session without a working hook is no gate at all."""
        claude_cli.cli_path()                       # raises a readable error when absent
        problem = self.check_gate()
        if problem:
            raise RuntimeError("The Allow / Deny gate is not working on this PC, so no "
                               "session was started: %s" % problem)
        notes: list[str] = []
        skill = self.skill_dir(notes.append)
        for n in notes:
            c.push({"kind": "notice", "text": n})
        st = S.load_session_state(c.assistant_dir)
        names = self.names(c.id)
        course = {"id": c.id, "name": self.course_name(c.id),
                  "base_url": self.cfg.base_url, "dir": c.dir,
                  "students": (f"This course has {len(names)} students, and they "
                               f"are numbered Student-1 to Student-{len(names)}."
                               if len(names) else
                               "No roster has been read for this course yet, so "
                               "any name in a message is one the Studio could "
                               "not swap. Treat every name you see as sensitive.")}
        sess = S.Session(course, self.port, self.secret,
                         sink=lambda ev, cc=c: self._on_event(cc, ev),
                         session_id=st["session_id"] if st else None, resume=bool(st),
                         model=model or getattr(self.cfg, "assistant_model", "") or None,
                         skill_dir=skill, cfg_path=getattr(self.cfg, "_path", None))
        sess.start()
        c.session = sess
        c.last_error = ""
        if not st:
            S.save_session_state(c.assistant_dir, sess.session_id)

    def _on_event(self, c: Course, ev: dict) -> None:
        kind = ev.get("kind")
        # A tag that arrives split across two streamed chunks would be turned
        # back into a name by neither of them, and the person would read the
        # tag. Re-cut the stream so no chunk ends part way through one.
        if kind == "text":
            ev = self._restream(c, ev)
            if ev is None:
                return
        elif c.mask_tail:
            with c.lock:
                held, c.mask_tail = c.mask_tail, ""
            c.push({"kind": "text", "text": held})
        with c.lock:
            if kind == "init":
                c.model = ev.get("model") or c.model
            elif kind == "result":
                c.busy = False
                if c.session:
                    S.save_session_state(c.assistant_dir, c.session.session_id,
                                         started=(S.load_session_state(c.assistant_dir) or {}).get("started"))
            elif kind == "exit":
                c.busy = False
                detail = ev.get("detail") or ""
                if not ev.get("stopped") and ev.get("code") not in (0, None):
                    if "not logged in" in detail.lower() or "/login" in detail:
                        c.last_error = ("Claude Code is not signed in on this PC. Open a terminal, "
                                        "run claude, then /login, and send your message again.")
                    else:
                        c.last_error = ("Claude Code closed unexpectedly (exit %s). Nothing in "
                                        "Canvas changes because of an error like this. Send your "
                                        "message again to continue." % ev.get("code"))
                    ev = dict(ev, text=c.last_error)
                # a resume of a session the CLI does not know is fatal; start fresh next time
                if not ev.get("stopped") and ev.get("code") not in (0, None) and \
                        "no conversation found" in detail.lower():
                    S.clear_session_state(c.assistant_dir)
                c.session = None
                # any question still open for this session dies with it
                for p in list(self.pending.values()):
                    if p.course_id == c.id and not p.event.is_set():
                        self._settle(p, "deny", "The Claude session ended before anyone answered.", auto=True)
            c.push(ev)

    def _restream(self, c: Course, ev: dict):
        """Hold back the end of a chunk while it could still become a tag.

        Returns the event to push, or None when the whole chunk is being held
        (which happens exactly when Claude has just written "Stud"). What is
        held always comes out: the next chunk carries it, and any other event
        flushes it first.
        """
        names = self.names(c.id)
        with c.lock:
            text = c.mask_tail + (ev.get("text") or "")
            keep = names.hold_len(text)
            c.mask_tail = text[len(text) - keep:] if keep else ""
            out = text[:len(text) - keep] if keep else text
        return dict(ev, text=out) if out else None

    def stop(self, course_id) -> dict:
        c = self.course(course_id)
        with c.lock:
            if c.session:
                c.session.stop()
            c.busy = False
            c.push({"kind": "notice", "text": "Stopped. Your conversation is kept; just send your next message."})
        return {"ok": True}

    def new(self, course_id) -> dict:
        """Forget the conversation: a fresh session id next time, the log
        rotated, the ring cleared. The workspace files stay."""
        c = self.course(course_id)
        with c.lock:
            if c.session:
                c.session.stopped = True
                c.session.stop()
                c.session = None
            c.busy = False
            for p in list(self.pending.values()):
                if p.course_id == c.id and not p.event.is_set():
                    self._settle(p, "deny", "The conversation was reset before anyone answered.", auto=True)
            S.clear_session_state(c.assistant_dir)
            c.rotate_conversation()
            c.ring.clear()
            c.mask_tail = ""
            c.model = ""
            c.last_error = ""
            c.push({"kind": "notice", "text": "New conversation. Claude does not remember the last one."})
        return {"ok": True, "seq": c.seq}

    # ---- permissions
    def permission_request(self, body: dict) -> dict:
        """The hook's long poll. Validates the secret, registers the question,
        waits for a click, and answers {"decision", "reason"}. Every failure
        is a deny; the hook treats anything but "allow" as a deny too."""
        if not isinstance(body, dict) or body.get("secret") != self.secret:
            return {"decision": "deny",
                    "reason": "Request did not come from this Studio's Claude session."}
        cid = str(body.get("course") or "")
        if not cid:
            cid = self._course_for_session(body.get("session_id"), body.get("cwd"))
        if not cid:
            return {"decision": "deny",
                    "reason": "This request does not belong to a running Assistant session."}
        c = self.course(cid)
        try:
            hook_timeout = float(body.get("timeout_s") or self.ask_timeout())
        except (TypeError, ValueError):
            hook_timeout = self.ask_timeout()
        wait = max(5.0, min(hook_timeout, self.ask_timeout()) - GRACE_S)
        p = Pending(uuid.uuid4().hex[:12], cid, body, wait)
        with self.lock:
            self.pending[p.id] = p
        view = p.view()
        view["kind"] = view.get("kind") or "unknown"
        view["headline"] = self.HEADLINES.get(view["kind"], "Claude wants to use a tool that may change something")
        c.push(dict(view, kind_gate=view["kind"], kind="permission"))
        if not p.event.wait(wait):
            self._settle(p, "deny",
                         "No answer in CourseForge Studio within %d minutes, so this was not run. "
                         "Ask the person whether to try it again." % max(1, int(wait // 60)),
                         auto=True)
        return {"decision": p.decision or "deny", "reason": p.reason or gate.DENY_TEXT}

    def _course_for_session(self, session_id, cwd) -> str:
        with self.lock:
            for c in self.courses.values():
                if c.session and session_id and c.session.session_id == session_id:
                    return c.id
            for c in self.courses.values():
                try:
                    if cwd and Path(cwd).resolve() == c.workspace.resolve():
                        return c.id
                except OSError:
                    continue
        return ""

    def answer(self, request_id: str, decision: str, reason: str = "") -> dict:
        with self.lock:
            p = self.pending.get(request_id)
        if p is None or p.event.is_set():
            raise KeyError("That request is no longer waiting for an answer.")
        decision = "allow" if str(decision).lower() == "allow" else "deny"
        self._settle(p, decision, reason if decision == "deny" and reason else
                     ("" if decision == "allow" else gate.DENY_TEXT))
        return {"ok": True, "decision": decision, "request_id": request_id}

    def _settle(self, p: Pending, decision: str, reason: str, auto: bool = False) -> None:
        with self.lock:
            if p.event.is_set():
                return
            p.decision, p.reason, p.auto = decision, reason, auto
            self.pending.pop(p.id, None)
            p.event.set()
        c = self.course(p.course_id)
        what = p.req.get("what") or p.req.get("summary") or ""
        c.push({"kind": "permission_answered", "request_id": p.id, "decision": decision,
                "auto": auto, "what": what, "gate_kind": p.req.get("kind")})
        if decision == "allow" and p.req.get("kind") == "canvas-write":
            # The action, not the content: what the person let through. The
            # verb itself records the write when it succeeds.
            try:
                ledger.record(c.dir, "assistant", "Allowed the Assistant to %s" % _lower_first(what),
                              kind="allow")
            except Exception:  # noqa: BLE001
                pass

    # ---- the hub card
    def hub_status(self, course_id) -> dict:
        c = self.course(course_id)
        st = S.load_session_state(c.assistant_dir) or {}
        n = len(self.pending_for(c.id))
        if n:
            lines = ["%d change%s waiting for your Allow or Deny" % (n, "" if n == 1 else "s")]
        elif st:
            lines = ["Conversation started %s, last used %s" % (st.get("started"), st.get("last_used"))]
        else:
            lines = ["No conversation yet. Tell it what to do; it asks before anything changes."]
        needs = [] if shutil.which("claude") else ["claude"]
        return {"lines": lines, "badge": n or None, "needs": needs,
                "actions": [{"label": "Open the Assistant", "href": "#/c/%s/assistant" % c.id}]}


def _lower_first(text: str) -> str:
    text = (text or "").strip()
    return text[:1].lower() + text[1:] if text else "run a command"


# ------------------------------------------------- an in-process listener

class PermissionListener:
    """A tiny HTTP server answering only POST /api/assistant/permission, for
    the console mode (no Studio server running) and for the tests. `handle`
    gets the parsed body and returns {"decision", "reason"}; the connection
    stays open while it thinks, exactly as the Studio's route does."""

    def __init__(self, handle: Callable[[dict], dict], port: int = 0):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                if self.path.rstrip("/") != "/api/assistant/permission":
                    return self._reply(404, {"error": "unknown endpoint"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except Exception:  # noqa: BLE001
                    body = {}
                try:
                    ans = outer.handle(body if isinstance(body, dict) else {})
                except Exception as exc:  # noqa: BLE001
                    ans = {"decision": "deny", "reason": "The permission listener failed: %s" % exc}
                self._reply(200, ans)

            def _reply(self, code, obj):
                data = json.dumps(obj).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:  # noqa: BLE001
                    pass

            def log_message(self, *_a):
                pass

        self.handle = handle
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        kwargs={"poll_interval": 0.2}, daemon=True)

    def start(self) -> "PermissionListener":
        self._thread.start()
        return self

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass
