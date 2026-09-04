"""
cf_assistant_hook.py - the permission gate for CourseForge Assistant.

Claude Code runs this as a PreToolUse hook (wired through --settings) before
EVERY tool call in an Assistant session. It decides on its own that reads,
searches, dumps and dry runs may proceed, and it asks the person - through a
real Allow / Deny dialog in the Assistant window - before anything that
writes to Canvas or changes something outside the course folder. That is the
PDF Fixer's rule ("anything destructive gets a real dialog") carried over.

Two halves, deliberately in one stdlib-only file:
  classify()   the pure rules. The app imports them to label activity in the
               conversation pane and the tests exercise them directly;
               nothing in here touches the network.
  main()       the hook process: stdin JSON -> classify -> allow, or ask the
               app over a localhost socket and relay the person's answer.

Fail closed. If the app cannot be reached the answer is deny, with a reason
Claude can read, and nothing runs. This file must stay importable by the
bundled embeddable Python (no third-party modules).
"""
import json
import os
import re
import socket
import sys

# Tools that only look at things. Always fine.
READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite",
    "TodoRead", "Task", "Agent", "Skill", "ToolSearch", "NotebookRead",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "ListAgents",
    "TaskOutput", "TaskStop", "Monitor", "Workflow", "SendMessage",
    "ScheduleWakeup", "PushNotification", "ReportFindings",
}
FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

# Skill scripts that write to Canvas the moment they run (no -Apply switch).
CANVAS_WRITE_ALWAYS = ("Push-CanvasPages", "Push-CanvasProject", "Trim-CanvasNav")

# Skill scripts that are a dry run WITHOUT -Apply and a Canvas write WITH it.
CANVAS_WRITE_WITH_APPLY = (
    "Push-CanvasRemediation", "Push-CanvasRubrics", "Set-DueDates",
    "Import-CanvasCourse", "Post-Grades", "Fix-BoldAsStructure",
    "Remove-BorderedBoxes", "Batch-Remediate", "Remediate-CanvasPptx",
    "Remediate-CanvasDocx", "Remediate-CanvasPdfText", "Remediate-OfficeText",
    "Fastlane-CanvasPdfs", "Check-SLOAlignment", "Backup-CanvasQuiz",
)

_APPLY = re.compile(r"(?<![\w-])(-Apply|--apply)(?=[\s:=]|$)", re.I)
_DRY = re.compile(r"(?<![\w-])(-WhatIf|-DryRun|--dry-run)(?=[\s:]|$)", re.I)

# HTTP writes from a shell - a script Claude wrote itself, a one-liner, or the
# skill's own helpers (Invoke-CanvasApi, Post-Canvas, Write-CanvasBody...).
# Live testing on 2026-09-04 showed that anchoring on cmdlet names misses
# wrappers, so every rule keys on the WRITE VERB wherever it appears; the cost
# is an occasional needless dialog, which is the safe side to err on.
_WRITE_VERB = r"(Put|Post|Delete|Patch)"
_HTTP_WRITES = [
    # -Method POST on ANY cmdlet or function (Invoke-RestMethod, irm, Invoke-CanvasApi, ...)
    re.compile(r"-Method\s*[:=]?\s*['\"]?" + _WRITE_VERB + r"\b", re.I),
    # splatted / hashtable / .NET / python keyword form:  Method = 'POST', method="put"
    re.compile(r"\bMethod\s*=\s*['\"]?" + _WRITE_VERB + r"\b", re.I),
    # curl / wget style verbs and data flags
    re.compile(r"(^|\s)(-X|--request)[\s=]*['\"]?" + _WRITE_VERB + r"\b", re.I),
    re.compile(r"\bcurl(\.exe)?\b[^\n]*?\s(-d|--data(-raw|-binary|-urlencode)?"
               r"|-F|--form|-T|--upload-file)\b", re.I),
    re.compile(r"\bwget\b[^\n]*?\s--(post-data|post-file|method=" + _WRITE_VERB + r")", re.I),
    # python requests / http.client / urllib
    re.compile(r"\brequests\.(put|post|delete|patch)\s*\(", re.I),
    re.compile(r"\.request\(\s*['\"]" + _WRITE_VERB + r"['\"]", re.I),
    # .NET clients
    re.compile(r"\.(Post|Put|Delete|Patch)Async\s*\(", re.I),
    re.compile(r"\.Upload(String|Data|File|Values)(Async)?\s*\(", re.I),
    re.compile(r"\[(System\.)?Net\.WebClient\][^\n]*\bUpload", re.I),
    re.compile(r"WebRequest\b[^\n]*\.Method\s*=", re.I),
    # the skill's helpers that write without naming a verb on the command line
    re.compile(r"\b(Post-Canvas|Send-CanvasForm|Send-Json|Write-CanvasBody|"
               r"Add-ModuleItem|Set-Tab)\b", re.I),
]
# Safety net: a Canvas API address in the same command as a bare write verb.
_CANVAS_API = re.compile(r"/api/v1/|instructure\.com|canvas", re.I)
_BARE_VERB = re.compile(r"(?<![\w-])(POST|PUT|DELETE|PATCH)(?![\w-])")


def _http_write(cmd):
    for rx in _HTTP_WRITES:
        if rx.search(cmd):
            return True
    return bool(_CANVAS_API.search(cmd) and _BARE_VERB.search(cmd))

# Changes to the PC itself, not to Canvas. Still worth a person's click.
_SYSTEM_CHANGES = [
    (re.compile(r"\b(Remove-Item|rm|del|erase|rmdir|rd)\b[^\n|;]*"
                r"(-Recurse\b|\s-r[f]?\b|\s-fr\b|\s/s\b)", re.I),
     "Delete a folder and everything in it"),
    (re.compile(r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|"
                r"checkout\s+--|branch\s+-D)\b", re.I),
     "Change a git repository"),
    (re.compile(r"\b(pip3?|python\s+-m\s+pip)\s+install\b|\bnpm\s+(install|i)\b|"
                r"\bwinget\s+install\b|\bchoco\s+install\b|\bmsiexec\b", re.I),
     "Install software on this PC"),
    (re.compile(r"\|\s*iex\b|\bInvoke-Expression\b", re.I),
     "Run downloaded code"),
    (re.compile(r"\b(Set-ExecutionPolicy|reg\s+(add|delete)|schtasks|"
                r"Stop-Computer|Restart-Computer|shutdown|net\s+user)\b", re.I),
     "Change a Windows setting"),
    (re.compile(r"\bSet-ItemProperty\b[^\n]*\bHK(LM|CU)\b", re.I),
     "Change the Windows registry"),
]

_SETUP = re.compile(r"\bSetup-Canvas(\.ps1)?\b", re.I)


def _verdict(decision, kind, summary, why):
    return {"decision": decision, "kind": kind, "summary": summary, "why": why}


def _shorten(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _first_line(cmd):
    for line in (cmd or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return (cmd or "").strip()


def summarize(tool_name, tool_input):
    """One plain line for the conversation pane: what Claude is doing."""
    ti = tool_input or {}
    if tool_name in SHELL_TOOLS:
        desc = (ti.get("description") or "").strip()
        return _shorten(desc or _first_line(ti.get("command", "")))
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    base = os.path.basename(path) if path else ""
    if tool_name == "Read":
        return "Reading %s" % (base or "a file")
    if tool_name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "Editing %s" % (base or "a file")
    if tool_name == "Write":
        return "Writing %s" % (base or "a file")
    if tool_name in ("Glob", "Grep"):
        pat = ti.get("pattern")
        return "Searching files" + (" for %s" % _shorten(pat, 40) if pat else "")
    if tool_name == "WebFetch":
        url = ti.get("url", "")
        host = re.sub(r"^https?://", "", url).split("/")[0]
        return "Fetching %s" % (host or "a web page")
    if tool_name == "WebSearch":
        return "Searching the web"
    if tool_name == "Skill":
        return "Using the %s skill" % (ti.get("skill") or "")
    if tool_name in ("Task", "Agent"):
        return "Starting a helper: %s" % _shorten(ti.get("description", ""), 60)
    if tool_name == "TodoWrite":
        return "Updating the task list"
    return tool_name


def _under(path, root):
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root))
        return p == r or p.startswith(r.rstrip("\\/") + os.sep)
    except Exception:
        return False


def _is_temp(path):
    low = os.path.normcase(os.path.abspath(path))
    for var in ("TEMP", "TMP"):
        v = os.environ.get(var)
        if v and _under(low, v):
            return True
    return ("\\temp\\" in low) or ("/tmp/" in low.replace("\\", "/"))


def classify(tool_name, tool_input, cwd):
    """Pure decision: allow, or ask the person. Never deny on its own - a
    deny is always a person's choice (or the app being unreachable)."""
    ti = tool_input or {}
    summary = summarize(tool_name, ti)

    if tool_name in READ_ONLY_TOOLS:
        return _verdict("allow", "read", summary, "read-only tool")

    if tool_name in FILE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        full = os.path.join(cwd, path) if path and not os.path.isabs(path) else path
        if path and (_under(full, cwd) or _is_temp(full)):
            return _verdict("allow", "local", summary, "file inside the course folder")
        return _verdict("ask", "local-change",
                        "Change a file outside the course folder: %s" % (path or "?"),
                        "writes outside the course folder need a person")

    if tool_name in SHELL_TOOLS:
        cmd = ti.get("command") or ""
        if _SETUP.search(cmd):
            return _verdict("ask", "system", summary,
                            "Setup-Canvas would open an interactive prompt; Canvas is "
                            "already connected by the app")
        low_hit = None
        for name in CANVAS_WRITE_ALWAYS:
            if re.search(r"\b%s\b" % re.escape(name), cmd, re.I):
                low_hit = name
                break
        if low_hit and not _DRY.search(cmd):
            return _verdict("ask", "canvas-write", summary,
                            "%s writes to Canvas as soon as it runs" % low_hit)
        if _APPLY.search(cmd):
            which = next((n for n in CANVAS_WRITE_WITH_APPLY
                          if re.search(r"\b%s\b" % re.escape(n), cmd, re.I)), None)
            return _verdict("ask", "canvas-write", summary,
                            "%s with -Apply changes the live course"
                            % (which or "This command"))
        if _http_write(cmd):
            return _verdict("ask", "canvas-write", summary,
                            "a direct web request that changes data")
        for rx, label in _SYSTEM_CHANGES:
            if rx.search(cmd):
                return _verdict("ask", "system", "%s: %s" % (label, summary),
                                label.lower())
        return _verdict("allow", "run", summary, "reads, dumps, transforms or dry run")

    # Unknown tool (an MCP server, a future built-in). Let the name decide.
    if re.search(r"delete|remove|write|create|update|send|post|put|upload|publish",
                 tool_name, re.I):
        return _verdict("ask", "unknown-write", "%s (%s)" % (tool_name, summary),
                        "an unfamiliar tool whose name suggests a change")
    return _verdict("allow", "unknown", summary, "unfamiliar tool, no sign of a change")


# ------------------------------------------------------------------ the hook

DENY_TEXT = ("The person clicked Deny in CourseForge Assistant. Do not retry it "
             "or work around it; tell them what you were about to do and ask "
             "what they would like instead.")


def _emit(decision, reason):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


def ask_app(payload, port, timeout_connect=10):
    """Send one JSON line to the Assistant window and wait (as long as the
    person takes) for one JSON line back."""
    with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout_connect) as s:
        s.settimeout(None)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        f = s.makefile("r", encoding="utf-8")
        line = f.readline()
    return json.loads(line) if line.strip() else {}


def main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception:
        _emit("deny", "CourseForge Assistant could not read this request; nothing was run.")
        return 0
    tool = req.get("tool_name") or ""
    ti = req.get("tool_input") or {}
    cwd = req.get("cwd") or os.getcwd()
    v = classify(tool, ti, cwd)
    if v["decision"] == "allow":
        _emit("allow", v["why"])
        return 0
    port = os.environ.get("CF_ASSISTANT_PORT")
    if not port:
        _emit("deny", "This needs the person's approval, but CourseForge Assistant "
                      "is not running to ask them. Nothing was run.")
        return 0
    payload = {"secret": os.environ.get("CF_ASSISTANT_SECRET", ""),
               "tool_name": tool, "tool_input": ti, "cwd": cwd,
               "summary": v["summary"], "why": v["why"], "kind": v["kind"],
               "tool_use_id": req.get("tool_use_id"),
               "session_id": req.get("session_id")}
    try:
        ans = ask_app(payload, port)
    except Exception as e:
        _emit("deny", "Could not reach CourseForge Assistant to ask for approval "
                      "(%s). Nothing was run." % e)
        return 0
    if ans.get("decision") == "allow":
        _emit("allow", "approved by the person in CourseForge Assistant")
    else:
        _emit("deny", ans.get("reason") or DENY_TEXT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
