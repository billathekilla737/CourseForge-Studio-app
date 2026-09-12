"""
hook.py - the PreToolUse process Claude Code runs for every tool call in an
Assistant session.

    python -m courseforge.assistant.hook        (or:  python <this file>)

Reads the hook JSON on stdin, asks gate.classify() whether the call may run,
and for an "ask" verdict POSTs the question to the Studio at

    http://127.0.0.1:<CF_STUDIO_PORT>/api/assistant/permission

then BLOCKS on that request until the person clicks Allow or Deny in the
browser (the server holds the connection open and answers with
{"decision": "allow"|"deny", "reason": ...}). The answer is relayed to Claude
Code as hookSpecificOutput.permissionDecision.

Environment set by the session: CF_STUDIO_PORT, CF_STUDIO_SECRET,
CF_STUDIO_COURSE, CF_ASSISTANT_CANVAS_HOST, CF_ASSISTANT_ASK_TIMEOUT.

Fail closed on every path: no port or secret -> deny; the Studio unreachable
-> deny; no answer within ASK_TIMEOUT -> deny; any exception -> deny with exit
0 (Claude Code treats a hook that exits non-zero, other than 2, as "proceed",
so a crash would be a bypass). The hook's own timeout stays SHORTER than the
hook timeout in settings.json, because when Claude Code's fires the tool call
goes ahead.

Standard library only, and importable by path: the settings.json Claude Code
loads names this file, and the session's working directory is the course
workspace, not the repo.
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from courseforge.assistant import gate  # noqa: E402

PERMISSION_PATH = "/api/assistant/permission"


def _emit(decision, reason):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


def ask_studio(payload, port, timeout_s=None):
    """POST one question to the Studio and wait for the person's answer. The
    server answers only when someone clicks (or its own timer denies), so the
    read blocks; past `timeout_s` of silence the answer is a deny, because
    Claude Code's own hook timeout would otherwise let the call through."""
    wait = gate.ASK_TIMEOUT if timeout_s is None else float(timeout_s)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (int(port), PERMISSION_PATH), data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "CourseForge-Studio-hook"}, method="POST")
    with urllib.request.urlopen(req, timeout=wait) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception:
        _emit("deny", "CourseForge Studio could not read this request; nothing was run.")
        return 0
    if not isinstance(req, dict):
        _emit("deny", "CourseForge Studio could not read this request; nothing was run.")
        return 0
    tool = req.get("tool_name")
    ti = req.get("tool_input")
    if not isinstance(tool, str) or not tool or not isinstance(ti, dict):
        _emit("deny", "CourseForge Studio could not read this tool call; nothing was run.")
        return 0
    cwd = str(req.get("cwd") or os.getcwd())
    v = gate.classify(tool, ti, cwd)
    if v["decision"] == "allow":
        _emit("allow", v["why"])
        return 0
    port = os.environ.get("CF_STUDIO_PORT")
    secret = os.environ.get("CF_STUDIO_SECRET")
    if not port or not secret:
        _emit("deny", "This needs the person's approval, but CourseForge Studio "
                      "is not running to ask them. Nothing was run.")
        return 0
    payload = {"secret": secret,
               "course": os.environ.get("CF_STUDIO_COURSE", ""),
               "tool_name": tool, "tool_input": ti, "cwd": cwd,
               "summary": v["summary"], "why": v["why"], "kind": v["kind"],
               "what": v["what"], "timeout_s": gate.ASK_TIMEOUT,
               "tool_use_id": req.get("tool_use_id"),
               "session_id": req.get("session_id")}
    try:
        ans = ask_studio(payload, port)
    except (socket.timeout, TimeoutError):
        _emit("deny", "No answer in CourseForge Studio within %d minutes, so this "
                      "was not run. Ask the person whether to try it again."
              % int(gate.ASK_TIMEOUT // 60))
        return 0
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            _emit("deny", "No answer in CourseForge Studio within %d minutes, so this "
                          "was not run. Ask the person whether to try it again."
                  % int(gate.ASK_TIMEOUT // 60))
        else:
            _emit("deny", "Could not reach CourseForge Studio to ask for approval "
                          "(%s). Nothing was run." % e)
        return 0
    except Exception as e:
        _emit("deny", "Could not reach CourseForge Studio to ask for approval "
                      "(%s). Nothing was run." % e)
        return 0
    if isinstance(ans, dict) and ans.get("decision") == "allow":
        _emit("allow", "approved by the person in CourseForge Studio")
    else:
        reason = ans.get("reason") if isinstance(ans, dict) else None
        _emit("deny", reason or gate.DENY_TEXT)
    return 0


def main():
    """Never let an exception escape: Claude Code treats a hook that exits
    non-zero (other than 2) as a non-blocking error and RUNS the tool."""
    try:
        return _main()
    except Exception as e:                       # noqa: BLE001 - fail closed
        try:
            _emit("deny", "CourseForge Studio's permission check failed (%s: %s); "
                          "nothing was run." % (type(e).__name__, e))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
