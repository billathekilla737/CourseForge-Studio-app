"""
cf_assistant_hook.py - the permission gate for CourseForge Assistant.

Claude Code runs this as a PreToolUse hook (wired through --settings) before
EVERY tool call in an Assistant session. It lets reads, searches, dumps, dry
runs and local transforms proceed on their own, and it asks the person -
through a real Allow / Deny dialog in the Assistant window - before anything
else. That is the PDF Fixer's rule ("anything destructive gets a real dialog")
carried over.

The gate is an ALLOW-LIST, not a deny-list. The first version keyed on write
verbs and let anything that matched no pattern run; a two-step "write a script
into the course folder, then run it" walked straight past it, and so did an
encoded command. Now a shell command runs unasked only when every piece of it
is one of:
  - a script from the CourseForge toolkit, called by its known name (and,
    when the app tells us where the toolkit lives, from that folder), without
    a write switch
  - a read-only cmdlet or alias from a short list, with any literal URL on the
    connected Canvas host
  - a local file operation whose every path is inside the course folder and is
    not a script, a Claude Code config file, or the app's own assistant\\ folder
Anything else - an unknown script, a one-liner, an encoded command, a .NET
call, a web request elsewhere - is a question for the person. File writes get
the same treatment: a script file or a config file written into the course
folder is a question, because the next step could run or load it.

Two halves, deliberately in one stdlib-only file:
  classify()   the pure rules. The app imports them to label activity in the
               conversation pane and the tests exercise them directly;
               nothing in here touches the network.
  main()       the hook process: stdin JSON -> classify -> allow, or ask the
               app over a localhost socket and relay the person's answer.

Fail closed, for real: any exception, a missing window, an unreachable
socket, or no answer within ASK_TIMEOUT seconds all produce a deny that
Claude can read. Claude Code treats a hook that crashes as "proceed", so
main() never lets an exception escape. This file must stay importable by the
bundled embeddable Python (no third-party modules).
"""
import json
import os
import re
import shlex
import socket
import sys

# Tools that only look at things, or that only organise Claude's own work.
# Sub-agents (Task/Agent) are fine: every tool call they make comes back
# through this hook. WebFetch is NOT here: it is an outbound request whose
# address the model chooses, so it is gated on the host below.
READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "LS", "WebSearch", "TodoWrite", "TodoRead",
    "Task", "Agent", "Skill", "ToolSearch", "NotebookRead",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "ListAgents",
    "TaskOutput", "TaskStop", "Monitor", "Workflow", "ReportFindings",
}
FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
WEB_TOOLS = {"WebFetch"}

# How long the hook waits for the person before it gives up and denies. Claude
# Code's own hook timeout (settings.json) is longer, and when THAT fires the
# tool call proceeds, so this one must always come first.
ASK_TIMEOUT = float(os.environ.get("CF_ASSISTANT_ASK_TIMEOUT") or 1200)

# --------------------------------------------------------- the toolkit's names

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

# Skill scripts that only read Canvas or work on local files.
CANVAS_READ_SCRIPTS = (
    "Dump-CanvasContent", "Export-CanvasCourse", "Get-TermCalendar",
    "Compute-DueDates", "Verify-Slots", "Triage-CanvasPdfs",
    "Build-GradingBundle", "CanvasContext", "CanvasToken",
)

# Scripts that open an interactive token prompt: never from an Assistant session.
CANVAS_SETUP_SCRIPTS = ("Setup-Canvas", "Extract-CanvasToken")

KNOWN_PS = {n.lower() for n in CANVAS_WRITE_ALWAYS + CANVAS_WRITE_WITH_APPLY
            + CANVAS_READ_SCRIPTS + CANVAS_SETUP_SCRIPTS}

# The skill's Python tools. They work on local files; uploads go through the
# PowerShell gateways above. courseforge_pdf.py is left out on purpose: its
# verbs upload to Canvas.
KNOWN_PY = {
    "restyle_html.py", "triage_pdf.py", "pdf_fastlane.py", "pdf_text_tool.py",
    "office_text_tool.py", "remediate_docx.py", "remediate_pptx.py",
    "extract_attachment_text.py", "slo_framework_tool.py",
}

# -Apply, or any unambiguous PowerShell prefix of it (-Ap, -App, -Appl), or
# --apply. PowerShell binds parameter prefixes, so "-Ap" applies just as well.
_APPLY = re.compile(r"(?<![\w-])(-A(?:p(?:p(?:l(?:y)?)?)?)?|--apply)(?=[\s:=]|$)", re.I)
_DRY = re.compile(r"(?<![\w-])(-WhatIf|-DryRun|--dry-run)(?=[\s:]|$)", re.I)

# --------------------------------------------------- write verbs (kept from v1)
# Still useful: they turn "ask" into a labelled Canvas-write question instead
# of a generic one, and they catch wrappers the allow-list never sees.
_WRITE_VERB = r"(Put|Post|Delete|Patch)"
_HTTP_WRITES = [
    re.compile(r"-Method\s*[:=]?\s*['\"]?" + _WRITE_VERB + r"\b", re.I),
    re.compile(r"\bMethod\s*=\s*['\"]?" + _WRITE_VERB + r"\b", re.I),
    re.compile(r"(^|\s)(-X|--request)[\s=]*['\"]?" + _WRITE_VERB + r"\b", re.I),
    re.compile(r"\bcurl(\.exe)?\b[^\n]*?\s(-d|--data(-raw|-binary|-urlencode)?"
               r"|-F|--form|-T|--upload-file)\b", re.I),
    re.compile(r"\bwget\b[^\n]*?\s--(post-data|post-file|method=" + _WRITE_VERB + r")", re.I),
    re.compile(r"\brequests\.(put|post|delete|patch)\s*\(", re.I),
    re.compile(r"\.request\(\s*['\"]" + _WRITE_VERB + r"['\"]", re.I),
    re.compile(r"\.(Post|Put|Delete|Patch)Async\s*\(", re.I),
    re.compile(r"\.Upload(String|Data|File|Values)(Async)?\s*\(", re.I),
    re.compile(r"\[(System\.)?Net\.WebClient\][^\n]*\bUpload", re.I),
    re.compile(r"WebRequest\b[^\n]*\.Method\s*=", re.I),
    re.compile(r"\b(Post-Canvas|Send-CanvasForm|Send-Json|Write-CanvasBody|"
               r"Add-ModuleItem|Set-Tab)\b", re.I),
]
_CANVAS_API = re.compile(r"/api/v1/|instructure\.com|canvas", re.I)
_BARE_VERB = re.compile(r"(?<![\w-])(POST|PUT|DELETE|PATCH)(?![\w-])")


def _http_write(cmd):
    for rx in _HTTP_WRITES:
        if rx.search(cmd):
            return True
    return bool(_CANVAS_API.search(cmd) and _BARE_VERB.search(cmd))


# Changes to the PC itself, not to Canvas. Labelled so the dialog reads well.
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
    (re.compile(r"\b(Set-ExecutionPolicy|reg(\.exe)?\s+(add|delete|import)|schtasks|"
                r"Stop-Computer|Restart-Computer|shutdown|net\s+user)\b", re.I),
     "Change a Windows setting"),
    (re.compile(r"\bSet-ItemProperty\b[^\n]*\bHK(LM|CU)\b", re.I),
     "Change the Windows registry"),
]

# Ways of running code the gate cannot read: encoded or built-up commands,
# .NET static calls, nested shells, script hosts. A question, never a pass.
# (powershell's own -Command / -EncodedCommand flags and python -c are judged
# where those commands are parsed, so that grep -E and the like stay usable.)
_OPAQUE = re.compile(
    r"\[char\]|FromBase64|::"
    r"|\bInvoke-Expression\b|(?<![\w-])iex\b"
    r"|\bInvoke-Command\b|\bInvoke-Item\b|\bStart-Process\b|\bStart-Job\b"
    r"|\bNew-Object\b|\bAdd-Type\b"
    r"|\bcmd(\.exe)?\s+/[ck]\b|\b(wscript|cscript|mshta|rundll32|regsvr32|"
    r"certutil|bitsadmin|wmic|pwsh)(\.exe)?\b",
    re.I)

_SETUP = re.compile(r"\b(Setup-Canvas|Extract-CanvasToken)(\.ps1)?\b", re.I)

# Commands that only read or compute. Cmdlet verbs first, then aliases.
_READ_VERBS = re.compile(
    r"^(Get|Select|Where|ForEach|Sort|Measure|Format|Group|Compare|Test|"
    r"Resolve|Split|Join|ConvertFrom|ConvertTo|Write|Out|Read|Import|Find|"
    r"Show|Wait)(-\w+)?$", re.I)
_READ_EXCLUDE = {"out-file", "import-module", "write-error", "read-host",
                 "import-clixml", "import-alias", "import-psession"}
_READ_ALIASES = {
    "ls", "dir", "gci", "cat", "type", "gc", "echo", "pwd", "cd", "set-location",
    "push-location", "pop-location", "pushd", "popd", "findstr", "where",
    "where.exe", "whoami", "hostname", "date", "measure", "select", "sort",
    "group", "sls", "true", "false", "exit", "return", "start-sleep",
    "sleep", "wc", "head", "tail", "grep", "cut", "tr", "uniq", "basename",
    "dirname", "realpath", "file", "stat", "test", "printf", "[", "seq",
}
_HTTP_READERS = {"invoke-restmethod", "invoke-webrequest", "irm", "iwr", "curl",
                 "curl.exe", "wget", "invoke-canvasapi", "get-canvaspaged"}
_GIT_READ = re.compile(r"^git\s+(status|log|diff|show|branch|rev-parse|ls-files|"
                       r"remote|describe|blame|tag)\b", re.I)

# Local file operations: fine inside the course folder, a question elsewhere.
_LOCAL_WRITERS = {
    "new-item", "ni", "mkdir", "md", "copy-item", "cp", "cpi", "copy",
    "move-item", "mv", "mi", "move", "rename-item", "ren", "rni",
    "set-content", "sc", "add-content", "ac", "out-file", "tee-object",
    "remove-item", "rm", "ri", "del", "erase", "rmdir", "rd", "clear-content",
    "expand-archive", "compress-archive", "touch", "unzip", "zip",
}

# Files whose contents become code or configuration for a LATER step.
_SCRIPT_EXT = (".ps1", ".psm1", ".psd1", ".ps1xml", ".py", ".pyw", ".pyc",
               ".cmd", ".bat", ".exe", ".com", ".dll", ".vbs", ".vbe", ".js",
               ".jse", ".wsf", ".wsh", ".msi", ".msc", ".scr", ".lnk", ".reg",
               ".sh", ".hta", ".pth", ".inf")
_CONTROL_NAMES = {"claude.md", "claude.local.md", ".mcp.json", "settings.json",
                  "settings.local.json", ".claude.json"}
_CONTROL_DIRS = {".claude", "assistant", ".git", ".vscode", ".idea"}

_URL = re.compile(r"https?://[^\s\"'<>()\]]+", re.I)


# ------------------------------------------------------------------ helpers

def _verdict(decision, kind, summary, why, what=None):
    return {"decision": decision, "kind": kind, "summary": summary, "why": why,
            "what": what or summary}


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
    """One plain line for the conversation pane: what Claude SAYS it is
    doing. For a shell that is the model's own description - fine for the
    pane, never for the dialog (see `what`)."""
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


def _what_for(tool_name, tool_input):
    """The gate's OWN headline for the dialog, from the tool input alone.
    Never the model's description: an injected model can call a destructive
    command 'Reading the syllabus'."""
    ti = tool_input or {}
    if tool_name in SHELL_TOOLS:
        return "Run: " + _shorten(_first_line(ti.get("command", "")), 160)
    if tool_name in FILE_TOOLS:
        return "%s file: %s" % ("Write" if tool_name == "Write" else "Edit",
                                ti.get("file_path") or ti.get("notebook_path") or "?")
    if tool_name in WEB_TOOLS:
        return "Fetch: " + _shorten(ti.get("url", "?"), 160)
    return "%s: %s" % (tool_name, _shorten(json.dumps(ti, sort_keys=True), 140))


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


def _canvas_host():
    return (os.environ.get("CF_ASSISTANT_CANVAS_HOST") or "").lower().strip()


def _skill_scripts_dir():
    d = os.environ.get("CF_ASSISTANT_SKILL") or ""
    return os.path.join(d, "scripts") if d else ""


def _expand(path, cwd):
    """Resolve the few variable forms Claude uses in paths. Anything else with
    a $ in it cannot be resolved and is treated as unknown."""
    p = path.strip().strip("\"'")
    home = os.path.expanduser("~")
    p = re.sub(r"\$HOME\b|\$env:USERPROFILE\b|\$\{HOME\}|^~(?=[\\/])", home.replace("\\", "\\\\"), p, flags=re.I)
    if "$" in p or "`" in p or "%" in p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def _local_path_problem(path, cwd):
    """None when a file path is a fine place for Claude to write on its own;
    otherwise the reason it is not."""
    full = _expand(path, cwd)
    if full is None:
        return "a path the gate cannot resolve"
    if not (_under(full, cwd) or _is_temp(full)):
        return "a file outside the course folder"
    low = os.path.normcase(full)
    parts = low.replace("/", "\\").split("\\")
    name = parts[-1]
    if name.endswith(_SCRIPT_EXT):
        return "a script or program file (it could be run in a later step)"
    if name in _CONTROL_NAMES:
        return "a Claude Code configuration file (it would change the next session)"
    rel_parts = parts
    try:
        rel = os.path.relpath(full, cwd)
        if not rel.startswith(".."):
            rel_parts = os.path.normcase(rel).replace("/", "\\").split("\\")
    except ValueError:
        pass
    for d in rel_parts[:-1]:
        if d in _CONTROL_DIRS:
            return "the %s folder, which the app and Claude Code read on the next session" % d
    return None


def _split_segments(cmd):
    """Pieces that each run something: newlines, ; | && and || outside quotes.
    A single & is NOT a separator - in PowerShell it is the call operator
    (& "x.ps1"), and splitting on it would leave a bare string literal that
    looks harmless. Separators inside quotes are text (grep -E "a|b")."""
    text = re.sub(r"`\r?\n", " ", cmd or "")          # PowerShell line continuation
    out, buf, quote, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c in "\r\n;" or (c == "|") or (c == "&" and text[i:i + 2] == "&&"):
            out.append("".join(buf))
            buf = []
            if c in "|&" and text[i:i + 2] in ("||", "&&"):
                i += 1
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _tokens(seg):
    """Whitespace-and-quote aware split that leaves backslashes alone (posix
    shlex would turn .\\Push-X.ps1 into .Push-X.ps1). Quotes stay on the
    tokens; the callers strip them where a name or path is compared."""
    try:
        return shlex.split(seg, posix=False)
    except ValueError:
        return seg.split()


def _inner_groups(s):
    """Top-level bracketed groups in a piece: $( ... ), ( ... ), { ... } and
    `...`. Script blocks and sub-expressions run commands of their own, so each
    one is judged as a command in its own right."""
    out, depth, start, opener = [], 0, None, None
    pairs = {"(": ")", "{": "}"}
    i = 0
    while i < len(s):
        c = s[i]
        if depth == 0 and c in pairs:
            depth, start, opener = 1, i + 1, c
        elif depth > 0:
            if c == opener:
                depth += 1
            elif c == pairs[opener]:
                depth -= 1
                if depth == 0:
                    out.append(s[start:i])
        i += 1
    out += re.findall(r"`([^`\r\n]+)`", s)
    return [g for g in out if g.strip()]


def _nested_problem(s, cwd):
    for g in _inner_groups(s):
        for seg in _split_segments(g):
            p = _segment_problem(seg, cwd)
            if p:
                return p
    return None


def _urls_problem(seg):
    host = _canvas_host()
    for u in _URL.findall(seg):
        h = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].split(":")[0].lower()
        if not u.lower().startswith("https://"):
            return "a web request over plain http (%s)" % h
        if host and h != host:
            return "a web request to %s, not the connected Canvas site" % h
    return None


def _script_problem(path, args, cwd):
    """A toolkit .ps1 by known name (and from the toolkit folder when the app
    says where that is). Returns (problem, kind) or (None, None)."""
    base = os.path.basename(path.strip("\"'")).lower()
    name = base[:-4] if base.endswith(".ps1") else base
    if name not in KNOWN_PS:
        return ("%s is not a script from the CourseForge toolkit" % base, "run")
    if name in {n.lower() for n in CANVAS_SETUP_SCRIPTS}:
        return ("Setup-Canvas would open an interactive prompt; Canvas is "
                "already connected by the app", "system")
    sdir = _skill_scripts_dir()
    if sdir:
        full = _expand(path, cwd)
        if full is None or not _under(full, sdir):
            return ("%s is not being run from the CourseForge toolkit folder" % base, "run")
    argtext = " ".join(args)
    if _APPLY.search(argtext) or _APPLY.search(path):
        pretty = next((n for n in CANVAS_WRITE_WITH_APPLY if n.lower() == name), base)
        return ("%s with -Apply changes the live course" % pretty, "canvas-write")
    for n in CANVAS_WRITE_ALWAYS:
        if n.lower() == name and not _DRY.search(argtext):
            return ("%s writes to Canvas as soon as it runs" % n, "canvas-write")
    return (None, None)


def _pyscript_problem(path, args, cwd):
    base = os.path.basename(path.strip("\"'")).lower()
    if base not in KNOWN_PY:
        return ("%s is not a tool from the CourseForge toolkit" % base, "run")
    sdir = _skill_scripts_dir()
    if sdir:
        full = _expand(path, cwd)
        if full is None or not _under(full, sdir):
            return ("%s is not being run from the CourseForge toolkit folder" % base, "run")
    if _APPLY.search(" ".join(args)):
        return ("%s with --apply changes files that go to Canvas" % base, "canvas-write")
    return (None, None)


def _segment_problem(seg, cwd):
    """None when one piece of a command may run unasked; else (why, kind)."""
    s = seg.strip()
    if not s or s.startswith("#"):
        return None
    # $x = <command>   -> judge the right-hand side
    m = re.match(r"^\$[\w:.\[\]]+\s*[-+*/]?=\s*(.*)$", s)
    if m:
        s = m.group(1).strip()
        if not s:
            return None
    # pure expressions: variables, literals, hashtables, arrays, string ops
    if re.match(r"^[\$@\"'\d(\[]", s) or s.lower() in ("true", "false"):
        if "&" in s or re.search(r"\bInvoke|\.Invoke\(", s, re.I):
            return ("a call the gate cannot verify", "run")
        return _nested_problem(s, cwd)
    toks = _tokens(s)
    if not toks:
        return None
    head = toks[0].strip("\"'")
    low = head.lower()
    args = [a.strip("\"'") if a[:1] in "\"'" else a for a in toks[1:]]

    # -- & "script" / . "script"  (call operator, dot-source): judge the target
    if low in ("&", "."):
        if not args:
            return ("a bare call operator", "run")
        head = args[0].strip("\"'")
        low = head.lower()
        args = args[1:]
        if not low.endswith(".ps1"):
            return ("%s called through the %s operator" % (head, toks[0]), "run")
    if "&" in args:
        return ("the & operator inside a command", "run")

    # -- a toolkit script named without .ps1 (a function of the same name)
    if low in KNOWN_PS:
        p = _script_problem(head, args, cwd)
        return p if p[0] else None

    # -- powershell -File <toolkit script>
    if low in ("powershell", "powershell.exe"):
        allowed_flags = {"-noprofile", "-nologo", "-noninteractive", "-executionpolicy",
                         "-nop", "-noni", "-ep", "-file", "-f", "-windowstyle", "-w",
                         "-inputformat", "-outputformat", "-mta", "-sta"}
        i, script = 0, None
        while i < len(args):
            a = args[i].lower()
            if a in ("-file", "-f"):
                script = args[i + 1] if i + 1 < len(args) else None
                rest = args[i + 2:]
                break
            if a in ("-executionpolicy", "-ep", "-windowstyle", "-w", "-inputformat",
                     "-outputformat"):
                i += 2
                continue
            if a not in allowed_flags:
                return ("powershell called with %s, which the gate cannot inspect" % args[i], "run")
            i += 1
        if not script:
            return ("powershell without -File (an inline command)", "run")
        p = _script_problem(script, rest, cwd)
        return p if p[0] else None

    # -- .\Some-Script.ps1 args  (direct invocation)
    if low.endswith(".ps1"):
        p = _script_problem(head, args, cwd)
        return p if p[0] else None

    # -- python <toolkit tool>
    if low in ("python", "python.exe", "py", "python3"):
        if not args:
            return ("an interactive python", "run")
        if args[0] in ("--version", "-V", "-VV"):
            return None
        if args[0].startswith("-"):
            return ("python %s (inline code or a module the gate cannot inspect)" % args[0], "run")
        p = _pyscript_problem(args[0], args[1:], cwd)
        return p if p[0] else None

    if low in ("claude", "claude.exe", "claude.cmd"):
        if args and args[0] in ("--version", "-v"):
            return None
        return ("a nested Claude session", "run")

    # -- git, read-only subcommands
    if low == "git":
        return None if _GIT_READ.match(s) else ("git %s" % (args[0] if args else ""), "run")

    # -- HTTP readers: no write verb (checked earlier), literal GET/HEAD only,
    #    every literal URL on the Canvas host, any saved output inside the folder
    if low in _HTTP_READERS:
        mm = re.search(r"-Method\s*[:=]?\s*(\S+)", s, re.I)
        if mm and mm.group(1).strip("\"'").upper() not in ("GET", "HEAD", "OPTIONS"):
            return ("a web request whose method the gate cannot read", "canvas-write")
        if re.search(r"(^|\s)(-X|--request)\b", s) and not re.search(
                r"(^|\s)(-X|--request)[\s=]*['\"]?(GET|HEAD)\b", s, re.I):
            return ("a web request whose method the gate cannot read", "canvas-write")
        u = _urls_problem(s)
        if u:
            return (u, "egress")
        for tok in _path_like(args):
            prob = _local_path_problem(tok, cwd)
            if prob:
                return ("%s saving to %s" % (head, prob), "local-change")
        return None

    # -- local file operations inside the course folder
    if low in _LOCAL_WRITERS or ">" in s:
        for tok in _path_like(args if low in _LOCAL_WRITERS else toks[1:]):
            prob = _local_path_problem(tok, cwd)
            if prob:
                return ("%s: %s" % (head, prob), "local-change")
        if low in _LOCAL_WRITERS and not _path_like(args):
            return ("%s without a path the gate can check" % head, "local-change")
        return _nested_problem(s, cwd)

    # -- read-only cmdlets and aliases (script blocks inside them are judged too)
    if low in _READ_ALIASES or (_READ_VERBS.match(head) and low not in _READ_EXCLUDE):
        u = _urls_problem(s)
        if u:
            return (u, "egress")
        return _nested_problem(s, cwd)

    return ("%s is not on the Assistant's list of read-only commands" % head, "run")


def _path_like(tokens):
    """Tokens that name a file: anything with a separator, an extension, or a
    ~ / . prefix. URLs are not paths (they are judged by _urls_problem)."""
    out = []
    for t in tokens:
        if re.match(r"^https?://", t, re.I):
            continue
        if t.startswith("-") and not re.match(r"^-\w+:", t):
            continue
        t = re.sub(r"^-\w+:", "", t)          # -Path:value
        if t in (">", ">>", "2>", "1>", "2>&1"):
            continue
        if "\\" in t or "/" in t or re.search(r"\.\w{1,6}$", t) or t.startswith(("~", ".")):
            out.append(t.strip("\"'"))
    return out


def classify(tool_name, tool_input, cwd):
    """Pure decision: allow, or ask the person. Never deny on its own - a
    deny is always a person's choice (or the app being unreachable)."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    summary = summarize(tool_name, ti)
    what = _what_for(tool_name, ti)

    if tool_name in READ_ONLY_TOOLS:
        return _verdict("allow", "read", summary, "read-only tool", what)

    if tool_name in WEB_TOOLS:
        url = str(ti.get("url") or "")
        host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0].lower()
        if url.lower().startswith("https://") and host and host == _canvas_host():
            return _verdict("allow", "read", summary, "a page on the connected Canvas site", what)
        return _verdict("ask", "egress", summary,
                        "a web request to %s, outside the connected Canvas site; "
                        "anything in the address leaves this PC" % (host or "an unknown address"),
                        what)

    if tool_name in FILE_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        prob = _local_path_problem(path, cwd) if path else "a file with no path"
        if prob is None:
            return _verdict("allow", "local", summary, "file inside the course folder", what)
        kind = "local-change"
        if "script" in prob or "configuration" in prob or "assistant" in prob or ".claude" in prob:
            kind = "local-script"
        return _verdict("ask", kind, "Change %s: %s" % (prob, path or "?"),
                        "writes of %s need a person" % prob, what)

    if tool_name in SHELL_TOOLS:
        cmd = ti.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return _verdict("ask", "run", summary,
                            "a shell call with no readable command", what)
        if _SETUP.search(cmd):
            return _verdict("ask", "system", summary,
                            "Setup-Canvas would open an interactive prompt; Canvas is "
                            "already connected by the app", what)
        if _http_write(cmd):
            return _verdict("ask", "canvas-write", summary,
                            "a direct web request that changes data", what)
        for rx, label in _SYSTEM_CHANGES:
            if rx.search(cmd):
                return _verdict("ask", "system", "%s: %s" % (label, summary),
                                label.lower(), what)
        if _OPAQUE.search(cmd):
            return _verdict("ask", "system", "Run code the gate cannot read: %s" % summary,
                            "an encoded, built-up or nested command the Assistant "
                            "cannot inspect", what)
        for seg in _split_segments(cmd):
            prob = _segment_problem(seg, cwd)
            if prob:
                why, kind = prob
                return _verdict("ask", kind, summary, why, what)
        return _verdict("allow", "run", summary,
                        "reads, dumps, transforms or a dry run from the toolkit", what)

    # Unknown tool (an MCP server, a future built-in): a person decides.
    return _verdict("ask", "unknown", "%s (%s)" % (tool_name, summary),
                    "an unfamiliar tool", what)


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


def ask_app(payload, port, timeout_connect=10, timeout_answer=None):
    """Send one JSON line to the Assistant window and wait for one line back,
    but not forever: past ASK_TIMEOUT the answer is a deny, because Claude
    Code's own hook timeout would otherwise let the call through."""
    wait = ASK_TIMEOUT if timeout_answer is None else timeout_answer
    with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout_connect) as s:
        s.settimeout(wait)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        f = s.makefile("r", encoding="utf-8")
        line = f.readline()
    return json.loads(line) if line.strip() else {}


def _main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception:
        _emit("deny", "CourseForge Assistant could not read this request; nothing was run.")
        return 0
    if not isinstance(req, dict):
        _emit("deny", "CourseForge Assistant could not read this request; nothing was run.")
        return 0
    tool = str(req.get("tool_name") or "")
    ti = req.get("tool_input")
    if not isinstance(ti, dict):
        _emit("deny", "CourseForge Assistant could not read this tool call; nothing was run.")
        return 0
    cwd = str(req.get("cwd") or os.getcwd())
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
               "what": v["what"], "timeout_s": ASK_TIMEOUT,
               "tool_use_id": req.get("tool_use_id"),
               "session_id": req.get("session_id")}
    try:
        ans = ask_app(payload, port)
    except socket.timeout:
        _emit("deny", "No answer in CourseForge Assistant within %d minutes, so this "
                      "was not run. Ask the person whether to try it again."
                      % int(ASK_TIMEOUT // 60))
        return 0
    except Exception as e:
        _emit("deny", "Could not reach CourseForge Assistant to ask for approval "
                      "(%s). Nothing was run." % e)
        return 0
    if isinstance(ans, dict) and ans.get("decision") == "allow":
        _emit("allow", "approved by the person in CourseForge Assistant")
    else:
        reason = ans.get("reason") if isinstance(ans, dict) else None
        _emit("deny", reason or DENY_TEXT)
    return 0


def main():
    """Never let an exception escape: Claude Code treats a hook that exits
    non-zero (other than 2) as a non-blocking error and RUNS the tool."""
    try:
        return _main()
    except Exception as e:                       # noqa: BLE001 - fail closed
        try:
            _emit("deny", "CourseForge Assistant's permission check failed (%s: %s); "
                          "nothing was run." % (type(e).__name__, e))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
