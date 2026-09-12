"""The Assistant from a terminal.

    python -m courseforge assistant ask --course ID "what to do"

The same session, the same gate. Permission requests print the gate's own
headline, the model's description, why it asks, and the exact command, then
wait on `Allow? [y/N] >`; an EOF (no terminal) is a No. The hook needs a
running permission endpoint, so this starts one in-process on a free port and
points the session at it. For scripted use and for diagnosing a PC.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from ..store import Store
from . import manager as M


def register(sub) -> None:
    p = sub.add_parser("assistant", help="talk to the Assistant from a terminal")
    p.set_defaults(area="assistant")
    v = p.add_subparsers(dest="verb", required=True)
    a = v.add_parser("ask", help="send one message to the course's Assistant session")
    a.add_argument("--course", required=True, help="Canvas course id")
    a.add_argument("prompt", help="what to do, in your own words")
    a.add_argument("--model", help="Claude model alias (default: Claude Code's own)")
    a.add_argument("--new", action="store_true", help="forget the previous conversation first")
    a.add_argument("--yes", action="store_true",
                   help="answer Allow to every permission question (scripted use only)")


class _ConsoleApp:
    """The three things the Manager needs from the App, without the server."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg.data)

    def course_dir(self, course_id) -> Path:
        return self.store.course_dir(course_id)


def run(args, cfg) -> int:
    if args.verb != "ask":
        print("unknown assistant verb %r" % args.verb, file=sys.stderr)
        return 2
    return ask_console(cfg, args.course, args.prompt, model=args.model,
                       fresh=args.new, yes_to_all=args.yes)


def ask_console(cfg, course_id, prompt, model=None, fresh=False, yes_to_all=False,
                out=None, ask=None) -> int:
    """Drive one turn from a console. `out` and `ask` are swappable for tests."""
    out = out or _stdout
    ask = ask or _ask_tty
    try:                       # console code pages mangle Claude's punctuation
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    app = _ConsoleApp(cfg)
    mgr = M.Manager(app)
    listener = M.PermissionListener(mgr.permission_request).start()
    mgr._port = listener.port
    cid = str(course_id)
    rc = 0
    try:
        if fresh:
            mgr.new(cid)
        try:
            mgr.send(cid, prompt, model=model)
        except Exception as exc:  # noqa: BLE001
            out("Could not start the Assistant: %s\n" % exc)
            return 2
        c = mgr.course(cid)
        seq = 0
        answered: set[str] = set()
        in_text = False
        while True:
            with c.lock:
                c.changed.wait(0.25)
            batch = mgr.events(cid, seq)
            for ev in batch["events"]:
                seq = ev["seq"]
                k = ev["kind"]
                if k == "text":
                    in_text = True
                    out(ev.get("text") or "")
                elif k == "text_end":
                    if in_text:
                        out("\n")
                    in_text = False
                elif k == "tool":
                    if in_text:
                        out("\n")
                        in_text = False
                    mark = "  > " if ev.get("decision") == "allow" else "  > (asking you) "
                    out(mark + (ev.get("summary") or ev.get("name") or "") + "\n")
                elif k == "tool_result" and ev.get("error") and ev.get("text"):
                    out("    problem: %s\n" % ev["text"][:160])
                elif k == "notice":
                    out("  ! %s\n" % ev.get("text", ""))
                elif k == "permission_answered":
                    if ev.get("auto"):
                        out("  [No answer, treated as Deny] %s\n" % ev.get("what", ""))
                elif k == "result":
                    if ev.get("error"):
                        out("ERROR: %s\n" % (ev.get("text") or "Claude stopped with an error."))
                        rc = 1
                elif k == "exit":
                    if ev.get("code") not in (0, None) and not ev.get("stopped"):
                        out("Claude Code exited %s: %s\n" % (ev.get("code"), ev.get("text") or ev.get("detail") or ""))
                        rc = 1
            for p in batch["pending"]:
                if p["request_id"] in answered:
                    continue
                answered.add(p["request_id"])
                if in_text:
                    out("\n")
                    in_text = False
                headline = mgr.HEADLINES.get(p.get("kind"), "Claude wants to use a tool that may change something")
                out("\n--- %s\n" % headline)
                out("    What: %s\n" % p.get("what"))
                if p.get("description"):
                    out("    Claude describes it as: %s\n" % p["description"])
                out("    Why it asks: %s\n" % p.get("why"))
                out("    %s\n" % (p.get("command") or "")[:600])
                decision = "allow" if yes_to_all else ask()
                try:
                    mgr.answer(p["request_id"], decision)
                except KeyError:
                    out("    (that request had already timed out)\n")
                out(("    allowed\n" if decision == "allow" else "    denied\n"))
            done = any(ev["kind"] in ("result", "exit") for ev in batch["events"])
            if done and not batch["pending"]:
                break
        mgr.stop(cid)
        time.sleep(0.2)
        return rc
    finally:
        listener.close()


def _stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _ask_tty() -> str:
    try:
        ans = input("Allow? [y/N] > ").strip().lower()
    except EOFError:
        ans = ""
    return "allow" if ans in ("y", "yes") else "deny"
