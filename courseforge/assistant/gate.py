"""
gate.py - the permission gate for the CourseForge Studio Assistant.

Claude Code runs this (through hook.py, wired in with --settings) as a
PreToolUse hook before EVERY tool call in an Assistant session. It lets reads,
searches, dumps, dry runs and local transforms proceed on their own, and it
asks the person - through the Allow / Deny card in the Studio's browser page -
before anything else.

The gate is an ALLOW-LIST, not a deny-list. The first version keyed on write
verbs and let anything that matched no pattern run; a two-step "write a script
into the folder, then run it" walked straight past it, and so did an encoded
command. A shell command runs unasked only when every piece of it is one of:
  - a Studio verb, `python -m courseforge <area> <verb> ...`, WITHOUT --apply
    (every writing verb is a dry run without it)
  - a read-only cmdlet or alias from a short list, with any literal URL on the
    connected Canvas host
  - a local file operation whose every path is inside the workspace folder and
    is not a script, a Claude Code config file, or the Studio's own assistant\\
    folder
Anything else - a Studio verb WITH --apply, an unknown command, a one-liner,
python -c, a PowerShell script, an encoded command, a .NET call, a web request
elsewhere, anything that touches grading data - is a question for the person.
File writes get the same treatment: a script file or a config file written into
the workspace is a question, because the next step could run or load it.

Two halves:
  classify()   the pure rules. The Studio imports them to label activity in the
               transcript and the tests exercise them directly; nothing in here
               touches the network.
  main()       the hook process, which lives in hook.py: stdin JSON ->
               classify -> allow, or ask the Studio over localhost HTTP and
               relay the person's answer. gate.main() delegates to it.

Fail closed, for real: any exception, no Studio to ask, an unreachable port, or
no answer within ASK_TIMEOUT seconds all produce a deny that Claude can read.
Claude Code treats a hook that crashes as "proceed", so main() never lets an
exception escape. This file stays standard-library only.
"""
import json
import os
import re
import shlex
import sys

# Tools that only look at things, or that only organise Claude's own work.
# Sub-agents (Task/Agent) are fine: every tool call they make comes back
# through this hook. WebFetch is gated on the Canvas host below. WebSearch is
# not here: a search query leaves this PC, so it is a question. The path-taking
# readers are allowed inside the workspace (and the skill folder) and are a
# question outside it, because the folder next door holds grading.
READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "LS", "TodoWrite", "TodoRead",
    "Task", "Agent", "Skill", "ToolSearch", "NotebookRead",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "ListAgents",
    "TaskOutput", "TaskStop", "Monitor", "Workflow", "ReportFindings",
}
PATH_READERS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
WEB_TOOLS = {"WebFetch"}

# How long the hook waits for the person before it gives up and denies. Claude
# Code's own hook timeout (settings.json) is longer, and when THAT fires the
# tool call proceeds, so this one must always come first.
ASK_TIMEOUT = float(os.environ.get("CF_ASSISTANT_ASK_TIMEOUT") or 1200)

# ------------------------------------------------------ the Studio's verbs

# `python -m courseforge <area> <verb> ...`. Every writing verb is a dry run
# without --apply and a Canvas write with it, so the list is one table: any
# known verb without --apply may run; any known verb with --apply is a question.
STUDIO_VERBS = {
    "a11y": {"dump", "restyle", "verify", "push", "restore", "bold-structure",
             "bordered-boxes", "batch"},
    "docs": {"list", "fetch", "describe", "push", "triage"},
    "pdf": {"list", "fetch", "fix", "figures", "describe", "apply-alt", "prove",
            "push", "rollback"},
    "content": {"draft", "place", "push-pages", "push-project", "rubrics",
                "verify-slots", "check-style", "check-quiz"},
    "course": {"export", "import", "clone", "nav", "due-dates", "quiz-backup", "slo"},
    # Reads, and pseudonymised at the source: the verb assembles its answer on
    # this machine and can only say Student-14. There is no --apply form.
    "students": {"list", "show"},
    "assistant": {"ask"},
}
# Top-level commands that only read: the doctor checks logins, courses lists them.
STUDIO_READ_COMMANDS = {"doctor", "courses"}
# Verbs that create a Canvas object without changing any course content. An
# export makes Canvas build a file; nothing a student sees moves. Allowed,
# and said so in the verdict.
STUDIO_OBJECT_ONLY = {("course", "export")}

# --apply, or any unambiguous prefix argparse would bind (--a, --ap, --app,
# --appl). Also the PowerShell forms, for a script that asks anyway.
_APPLY = re.compile(r"(?<![\w-])(--a(?:p(?:p(?:l(?:y)?)?)?)?|-A(?:p(?:p(?:l(?:y)?)?)?)?)(?=[\s:=]|$)", re.I)
_DRY = re.compile(r"(?<![\w-])(-WhatIf|-DryRun|--dry-run)(?=[\s:]|$)", re.I)
_COURSE_ARG = re.compile(r"(?<![\w-])--course(?:[\s=]+|$)(\d+)?", re.I)

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
    re.compile(r"\b\w[\w-]*\b[^\n]*\s-Verb\s+['\"]?" + _WRITE_VERB + r"\b", re.I),
]
_CANVAS_API = re.compile(r"/api/v1/|instructure\.com|canvas", re.I)
_BARE_VERB = re.compile(r"(?<![\w-])(POST|PUT|DELETE|PATCH)(?![\w-])")


def _http_write(cmd):
    for rx in _HTTP_WRITES:
        if rx.search(cmd):
            return True
    return bool(_CANVAS_API.search(cmd) and _BARE_VERB.search(cmd))


# Changes to the PC itself, not to Canvas. Labelled so the card reads well.
_SYSTEM_CHANGES = [
    (re.compile(r"\b(Remove-Item|rm|del|erase|rmdir|rd)\b[^\n|;]*"
                r"(-Recurse\b|\s-r[f]?\b|\s-fr\b|\s/s\b)", re.I),
     "Delete a folder and everything in it"),
    (re.compile(r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|"
                r"checkout\s+--|branch\s+-D)\b", re.I),
     "Change a git repository"),
    (re.compile(r"\b(pip3?|python\s+-m\s+pip|uv\s+pip)\s+install\b|\bnpm\s+(install|i)\b|"
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

# The Canvas token lives in the Studio's per-user store and never moves.
# Anything that names a token file or the token variable is a question.
_TOKEN = re.compile(r"canvas\.token|CANVAS_TOKEN|\.token\.enc\b|\btoken-[\w.-]+\.bin\b|"
                    r"\bsecrets\.json\b|Setup-Canvas|Extract-CanvasToken", re.I)

# Grading is the Studio's own screens. The Assistant never touches it: not the
# gradebook, not the per-assignment folders next to the workspace, not the
# pseudonym map or the draft grades. "graded discussion" is content and passes.
# These match ACCESS TARGETS (paths, URLs, filenames) — never assignment prose
# in --title / --brief. Bare words like "grading" or "submission" are course
# language; extracted.json is a student record.
_STUDENT_FILES = re.compile(
    r"(?:^|[\\/])(?:map|names|accommodations|extracted)\.json(?:$|[\\/\s\"'?#])"
    r"|proposed-grades",
    re.I)
_STUDENT_PATHS = re.compile(
    r"(?:^|[\\/])data[\\/]\d+[\\/]\d+(?:[\\/]|$)"
    r"|\bgradebook\b"
    r"|[\\/]grades?(?:[\\/?#]|$)"
    r"|[\\/]submissions?(?:[\\/?#]|$)",
    re.I)
# argparse flags whose values are instructor prose, not files.
_PROSE_FLAGS = {
    "--brief", "--title", "--body", "--prompt", "--message",
    "--comment", "--note", "--instructions", "--why", "--look",
}

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
    "sleep", "wc", "head", "tail", "grep", "rg", "cut", "tr", "uniq", "basename",
    "dirname", "realpath", "file", "stat", "test", "printf", "[", "seq", "tree",
}
_HTTP_READERS = {"invoke-restmethod", "invoke-webrequest", "irm", "iwr", "curl",
                 "curl.exe", "wget", "invoke-canvasapi", "get-canvaspaged"}
_GIT_READ = re.compile(r"^git\s+(status|log|diff|show|branch|rev-parse|ls-files|"
                       r"remote|describe|blame|tag)\b", re.I)

# Local file operations: fine inside the workspace, a question elsewhere.
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
                  "settings.local.json", ".claude.json", "session.json",
                  "system-prompt.md", "config.json"}
_CONTROL_DIRS = {".claude", "assistant", ".git", ".vscode", ".idea"}

_URL = re.compile(r"https?://[^\s\"'<>()\]]+", re.I)


# ------------------------------------------------------------------ helpers

def _verdict(decision, kind, summary, why, what=None):
    return {"decision": decision, "kind": kind, "summary": summary, "why": why,
            "what": what or summary}


def _is_student_data_target(text):
    """True when `text` names a gradebook file, identity map, assignment
    folder, or a live Canvas grades/submissions URL — not when it is a
    sentence that happens to say "grading"."""
    if not text:
        return False
    t = str(text).strip().strip("\"'")
    return bool(_STUDENT_FILES.search(t) or _STUDENT_PATHS.search(t))


def _strip_prose_args(cmd):
    """Drop --title / --brief / --body and their values so detectors never
    see assignment prose."""
    toks = _tokens(cmd or "")
    out, skip = [], False
    for tok in toks:
        if skip:
            skip = False
            continue
        name = tok.strip("\"'")
        if name.startswith("--") and "=" in name:
            if name.split("=", 1)[0].lower() in _PROSE_FLAGS:
                continue
        elif name.lower() in _PROSE_FLAGS:
            skip = True
            continue
        out.append(tok)
    return " ".join(out)


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
    """One plain line for the transcript: what Claude SAYS it is doing. For a
    shell that is the model's own description - fine for the transcript,
    never for the Allow / Deny card (see `what`)."""
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
    """The gate's OWN headline for the card, from the tool input alone.
    Never the model's description: an injected model can call a destructive
    command 'Reading the syllabus'."""
    ti = tool_input or {}
    if tool_name in SHELL_TOOLS:
        return "Run: " + _shorten(_first_line(ti.get("command", "")), 160)
    if tool_name in FILE_TOOLS:
        return "%s file: %s" % ("Write" if tool_name == "Write" else "Edit",
                                ti.get("file_path") or ti.get("notebook_path") or "?")
    if tool_name in PATH_READERS:
        return "Read: %s" % (ti.get("file_path") or ti.get("notebook_path")
                             or ti.get("path") or "?")
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


def _skill_dir():
    return os.environ.get("CF_ASSISTANT_SKILL") or ""


def _connected_course():
    return os.environ.get("CF_STUDIO_COURSE") or ""


def _course_dir(cwd):
    """The course folder when cwd is its workspace/. Else None.

    Content drafts live in <course>/build/, next to workspace/, not in the
    grading folders (numeric assignment ids).
    """
    try:
        base = os.path.normpath(os.path.abspath(cwd or ""))
    except Exception:
        return None
    if os.path.basename(base).lower() != "workspace":
        return None
    parent = os.path.dirname(base)
    return parent or None


# Course-level folders the Assistant may READ unasked. They hold content it
# just drafted or dumped, not student records. Writes stay limited to
# workspace/ plus build/ (non-scripts) so a draft JSON can be edited.
_CONTENT_READ_DIRS = ("build", "a11y")


def _under_course_content(full, cwd, names=_CONTENT_READ_DIRS):
    root = _course_dir(cwd)
    if not root:
        return False
    return any(_under(full, os.path.join(root, name)) for name in names)


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
    if not (_under(full, cwd) or _is_temp(full)
            or _under_course_content(full, cwd, names=("build",))):
        return "a file outside the workspace folder"
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
            return "the %s folder, which the Studio and Claude Code read on the next session" % d
    return None


def _read_path_problem(path, cwd):
    """None when a path may be READ unasked: inside the workspace, the course
    build/ (content drafts) or a11y/ dumps, temp, or the installed skill.
    Numeric assignment folders next door still hold grading."""
    full = _expand(path, cwd)
    if full is None:
        return "a path the gate cannot resolve"
    if _under(full, cwd) or _is_temp(full) or _under_course_content(full, cwd):
        return None
    sdir = _skill_dir()
    if sdir and _under(full, sdir):
        return None
    return "a file outside the workspace folder"


def _split_segments(cmd):
    """Pieces that each run something: newlines, ; | && and || outside quotes.
    A single & is NOT a separator - in PowerShell it is the call operator
    (& "x.ps1"), and splitting on it would leave a bare string literal that
    looks harmless. Separators inside quotes are text (grep -E "a|b")."""
    text = re.sub(r"`\r?\n", " ", cmd or "")          # PowerShell line continuation
    text = re.sub(r"\\\r?\n", " ", text)              # sh line continuation
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
    shlex would turn .\\x.ps1 into .x.ps1). Quotes stay on the tokens; the
    callers strip them where a name or path is compared."""
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


def _course_of(args):
    """The course id a Studio verb names (--course N), else the connected one."""
    m = _COURSE_ARG.search(" ".join(args))
    if m and m.group(1):
        return m.group(1)
    return _connected_course() or "?"


def _studio_problem(args, seg):
    """`python -m courseforge ARGS` judged by the Studio's own table. Returns
    (problem, kind[, what]) or (None, None)."""
    args = list(args)
    # an explicit config path is fine in front of the area
    if args and args[0].lower().startswith("--config"):
        args = args[1:] if "=" in args[0] else args[2:]
    if not args:
        return ("python -m courseforge alone starts the web server", "run")
    head = args[0].strip("\"'").lower()
    if head in STUDIO_READ_COMMANDS:
        return (None, None)
    if head in ("serve", "gui") or head.startswith("-"):
        return ("python -m courseforge %s starts a program, not a verb" % head, "run")
    if head not in STUDIO_VERBS:
        return ("%s is not an area of CourseForge Studio" % head, "run")
    verb = args[1].strip("\"'").lower() if len(args) > 1 else ""
    if verb in ("-h", "--help"):
        return (None, None)
    if verb not in STUDIO_VERBS[head]:
        return ("%s %s is not a verb the Studio knows" % (head, verb or "(none)"), "run")
    if head == "assistant":
        return ("a nested Assistant session", "run")
    rest = args[2:]
    # An argument the shell would still expand or reassemble is not one the
    # gate can read: `$env:X`, `%X%`, `$(echo --apply)` and a backtick escape
    # all reach the verb as --apply while this text says nothing of the kind.
    # A quote split through the flag ('--ap'ply) is the same trick, so the
    # flag is looked for with the quotes taken out. Instructor prose (--brief,
    # --title) may contain $20 or (optional) without being a shell expansion.
    skip_value = False
    for a in rest:
        if skip_value:
            skip_value = False
            continue
        name = a.strip("\"'")
        flag = name.split("=", 1)[0].lower()
        if name.startswith("--") and "=" in name and flag in _PROSE_FLAGS:
            continue
        if flag in _PROSE_FLAGS:
            skip_value = True
            continue
        if re.search(r"[$`%()]", a):
            return ("a built-up argument to a Studio verb that the gate cannot read", "run")
    # --apply inside --brief "never --apply this" is prose, not a live write.
    if _APPLY.search(re.sub(r"[\"']", "", _strip_prose_args(" ".join(rest)))):
        cid = _course_of(rest)
        return ("%s %s with --apply changes the live Canvas course" % (head, verb),
                "canvas-write",
                "Apply %s %s to Canvas course %s: %s" % (head, verb, cid, _first_line(seg)))
    return (None, None)


def _segment_problem(seg, cwd):
    """None when one piece of a command may run unasked; else (why, kind) or
    (why, kind, what)."""
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

    # -- env prefix: VAR=value command   (sh) -> judge the command
    while re.match(r"^[A-Za-z_]\w*=", head) and args:
        head = args[0].strip("\"'")
        low = head.lower()
        args = args[1:]

    # -- & "script" / . "script"  (call operator, dot-source): nothing the
    #    Studio ships is run that way, so it is always a question
    if low in ("&", "."):
        return ("%s called through the %s operator" % (args[0] if args else "nothing", toks[0]), "run")
    if "&" in args:
        return ("the & operator inside a command", "run")

    # -- PowerShell, inline or a script file: the Studio's verbs are Python
    if low in ("powershell", "powershell.exe", "pwsh", "pwsh.exe") or low.endswith(".ps1"):
        return ("a PowerShell script or inline command; the Studio's verbs are "
                "python -m courseforge ...", "run")

    # -- python: only the Studio's own command line, never a file or a one-liner
    if low in ("python", "python.exe", "py", "py.exe", "python3", "python3.exe"):
        if not args:
            return ("an interactive python", "run")
        if args[0] in ("--version", "-V", "-VV"):
            return None
        if args[0] == "-m":
            if len(args) < 2:
                return ("python -m with no module", "run")
            if args[1].strip("\"'").lower() == "courseforge":
                p = _studio_problem(args[2:], s)
                return p if p[0] else None
            return ("python -m %s (a module the gate cannot inspect)" % args[1], "run")
        if args[0].startswith("-"):
            return ("python %s (inline code or a flag the gate cannot inspect)" % args[0], "run")
        return ("%s is a python file, not the Studio's command line" % os.path.basename(args[0]), "run")

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
        for addr in _URL.findall(s):
            if _is_student_data_target(addr):
                return ("grading data", "student-data")
        # only what the request SAVES is a path; -Uri values are addresses
        for i, a in enumerate(args):
            if a.lower() in ("-o", "--output", "-outfile", "-outfile:") and i + 1 < len(args):
                prob = _local_path_problem(args[i + 1], cwd)
                if prob:
                    return ("%s saving to %s" % (head, prob), "local-change")
            elif a.lower().startswith("-outfile:"):
                prob = _local_path_problem(a.split(":", 1)[1], cwd)
                if prob:
                    return ("%s saving to %s" % (head, prob), "local-change")
            elif a in ("-O", "--remote-name", "-J"):
                return ("%s saving under a name the server chooses" % head, "local-change")
        return None

    # -- local file operations inside the workspace
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
        for addr in _URL.findall(s):
            if _is_student_data_target(addr):
                return ("grading data", "student-data")
        for tok in _path_like(args):
            if _is_student_data_target(tok):
                return ("%s: grading data" % head, "student-data")
            prob = _read_path_problem(tok, cwd)
            if prob:
                return ("%s: %s" % (head, prob), "read")
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
    deny is always a person's choice (or the Studio being unreachable)."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    summary = summarize(tool_name, ti)
    what = _what_for(tool_name, ti)

    if tool_name in READ_ONLY_TOOLS:
        if tool_name in PATH_READERS:
            path = str(ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "")
            if path:
                if _is_student_data_target(path):
                    return _verdict("ask", "student-data", summary,
                                    "grading data; grades are the Studio's own screens and "
                                    "never go through the Assistant", what)
                prob = _read_path_problem(path, cwd)
                if prob:
                    return _verdict("ask", "read", summary,
                                    "reading %s; the folders next to the workspace hold "
                                    "grading" % prob, what)
        return _verdict("allow", "read", summary, "read-only tool", what)

    if tool_name in WEB_TOOLS:
        url = str(ti.get("url") or "")
        host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0].lower()
        if url.lower().startswith("https://") and host and host == _canvas_host():
            if _is_student_data_target(url):
                return _verdict("ask", "student-data", summary,
                                "grading data; grades are the Studio's own screens and "
                                "never go through the Assistant", what)
            return _verdict("allow", "read", summary, "a page on the connected Canvas site", what)
        return _verdict("ask", "egress", summary,
                        "a web request to %s, outside the connected Canvas site; "
                        "anything in the address leaves this PC" % (host or "an unknown address"),
                        what)

    if tool_name in FILE_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if path and _is_student_data_target(path):
            return _verdict("ask", "student-data", "Change grading data: %s" % path,
                            "grading data; grades are the Studio's own screens and never "
                            "go through the Assistant", what)
        prob = _local_path_problem(path, cwd) if path else "a file with no path"
        if prob is None:
            return _verdict("allow", "local", summary, "file inside the workspace folder", what)
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
        scanned = _strip_prose_args(cmd)
        if _TOKEN.search(scanned):
            return _verdict("ask", "system", summary,
                            "touches the Canvas token; Canvas is already connected in the "
                            "Studio and the token never moves", what)
        if _http_write(scanned):
            return _verdict("ask", "canvas-write", summary,
                            "a direct web request that changes data", what)
        for rx, label in _SYSTEM_CHANGES:
            if rx.search(scanned):
                return _verdict("ask", "system", "%s: %s" % (label, summary),
                                label.lower(), what)
        if _OPAQUE.search(scanned):
            return _verdict("ask", "system", "Run code the gate cannot read: %s" % summary,
                            "an encoded, built-up or nested command the Assistant "
                            "cannot inspect", what)
        export_only = False
        for seg in _split_segments(cmd):
            prob = _segment_problem(seg, cwd)
            if prob:
                why, kind = prob[0], prob[1]
                return _verdict("ask", kind, summary, why, prob[2] if len(prob) > 2 else what)
            if re.search(r"\bcourseforge\s+course\s+export\b", seg, re.I):
                export_only = True
        if export_only:
            return _verdict("allow", "run", summary,
                            "course export asks Canvas to build an export file; no course "
                            "content changes", what)
        return _verdict("allow", "run", summary,
                        "reads, dumps, transforms or a dry run from the Studio's verbs", what)

    # Unknown tool (an MCP server, a future built-in): a person decides.
    return _verdict("ask", "unknown", "%s (%s)" % (tool_name, summary),
                    "an unfamiliar tool", what)


# ------------------------------------------------------------------ the hook

DENY_TEXT = ("The person clicked Deny in CourseForge Studio. Do not retry it "
             "or work around it; tell them what you were about to do and ask "
             "what they would like instead.")


def main():
    """The hook entry, for `python -m courseforge.assistant.gate` and for any
    settings.json still naming this file. The process half lives in hook.py;
    this never lets an exception escape, because Claude Code treats a hook
    that crashes as 'proceed'."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        from courseforge.assistant import hook
        return hook.main()
    except Exception as e:                       # noqa: BLE001 - fail closed
        try:
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "permissionDecisionReason":
                                          "CourseForge Studio's permission check failed (%s: %s); "
                                          "nothing was run." % (type(e).__name__, e)}}
            sys.stdout.write(json.dumps(out))
            sys.stdout.flush()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
