# Canvas Module Toolkit

A portable, model-agnostic toolkit for updating Canvas LMS course content: restyle a
page/assignment/quiz to your institution's brand, refresh material for currency, and
catch/fix quiz scoring or factual errors — with deterministic scripts doing the
mechanical work, so an AI agent only spends tokens on the judgment calls that actually
need a model.

It was extracted from real, live use restyling and fact-checking an eight-module
Canvas course, including catching a live scoring bug (a quiz's correct-answer key
pointing at the wrong law) and a wrong document citation, both fixed before students
ever saw them, plus a step-by-step "make Module 6 cloud-relevant" style content pass.

**Works with:** Claude Code, OpenAI Codex CLI, and — because it's just files, plain
JSON, and cross-platform scripts, with instructions in the open **[AGENTS.md](https://agents.md/)**
standard format — essentially any AI coding agent with shell and file access (Cursor,
Aider, Windsurf, GitHub Copilot, Gemini CLI, etc.), or a human with a terminal and no
AI agent at all.

## What's in here

```
canvas-module-toolkit/
  AGENTS.md                    <- THE playbook. Read by Codex + 25 other agents natively.
  CLAUDE.md                    <- 3-line adapter: imports AGENTS.md for Claude Code
  SKILL.md                     <- optional: install this as a Claude Code Skill/plugin
  README.md                    <- this file (human setup guide)
  scripts/
    lib_canvas.ps1              shared Canvas REST helpers (dot-source this)
    Get-CanvasModule.ps1        read-only: dump a module's items/bodies/quiz+keys
    Push-CanvasModule.ps1       dry-run-first: write page/assignment/quiz changes
    check_style.py              deterministic HTML/a11y/Canvas-sanitizer compliance
    check_quiz.py               quiz answer-key + question-count validator
    diff_content.py             word-level "did the rewrite drop content?" checker
    check_contrast.py           WCAG contrast ratio checker
  references/
    style-guide.example.md      one worked example brand template (navy/gold) - copy & adapt
    palette.example.txt         matching palette file for check_style.py --palette
    changeset-schema.md         the JSON formats Push-CanvasModule.ps1 / quiz files use
  examples/
    changeset.example.json      fill-in-the-blanks template
```

Nothing in `scripts/` or `references/` is Claude-specific or Codex-specific. `AGENTS.md`
is the only file that talks about "an agent" at all, and it's written to be read by any
of them.

## Prerequisites

- **PowerShell 7+** (`pwsh`). Windows PowerShell 5.1 also works on Windows.
  - macOS: `brew install --cask powershell`
  - Linux: see [PowerShell's install docs](https://learn.microsoft.com/powershell/scripting/install/installing-powershell)
- **Python 3** (`python3`) — standard library only, nothing to `pip install`.
- **A Canvas API token** for the account doing the work (Canvas → Account → Settings →
  New Access Token), saved locally as described below. **Never commit or paste this
  into a chat.**

## One-time setup: Canvas credentials

Create a folder (any name) with two files:

**`canvas.config.<courseId>.json`**
```json
{ "base_url": "https://YOURSCHOOL.instructure.com", "course_id": "12345" }
```

**`canvas.token`** — one line, just the token. Then, in that same repo, add a
`.gitignore` entry for both `canvas.token` and `canvas.config.*.json` if this folder is
ever committed anywhere.

The toolkit's scripts look for these in the current directory first, then in
`~/Documents/canvas-work` — or pass `-ConfigPath` / `-TokenPath` explicitly.

---

## Using it with Claude Code

**Option A — project memory (fastest, per-project).** Copy this whole
`canvas-module-toolkit/` folder into (or symlink it into) the repo you're working in,
and make sure `CLAUDE.md` at your project root imports it:
```
@canvas-module-toolkit/AGENTS.md
```
(If `canvas-module-toolkit/` *is* your project root, its own `CLAUDE.md` already does
this — nothing else to do.) Claude Code loads `CLAUDE.md` automatically at session
start; the `@import` pulls in the full `AGENTS.md` playbook.

**Option B — install as a Skill (reusable across every project).** Copy the whole
folder to `~/.claude/skills/canvas-module-toolkit/` (or wire it into a plugin
marketplace the way this repo's own `courseforge` skill is installed — see this
repo's `Install-CourseForge.ps1` for a worked pattern). `SKILL.md`'s frontmatter is
already written for the skill picker; Claude Code will surface it whenever you ask
it to update, restyle, or refresh a Canvas module.

Either way, once loaded, just ask in plain language: *"Update Module 5 to the branded
style and make sure the quiz is current."* Claude Code will follow `AGENTS.md`.

## Using it with OpenAI Codex CLI

Codex reads `AGENTS.md` **natively** — no adapter file needed. Put (or symlink) this
folder's `AGENTS.md` at whichever level fits how you work:

- **Per-project:** `your-repo/canvas-module-toolkit/AGENTS.md` (Codex merges any
  `AGENTS.md` it finds walking up the tree from the file it's touching, closest wins).
- **Global, for every project:** `~/AGENTS.md` (or wherever your Codex version's global
  instructions file lives — run `codex --init` / `/init` inside a repo if you want Codex
  to scaffold one for you, then paste this file's contents in, or just `@`-reference it
  if your version supports that).

**Two Codex-specific things to configure before it can actually run this:**

1. **Shell + filesystem access.** Codex's default "Auto" sandbox is
   `workspace-write` — it can already run `pwsh`/`python3` and edit files inside your
   working directory without asking. If you've tightened your sandbox to read-only,
   loosen it for this task, or approve the `pwsh`/`python3` calls when prompted.
2. **Network access.** Codex's sandbox has **network access off by default.** This
   toolkit needs the network for every Canvas API call (`Get-CanvasModule.ps1`,
   `Push-CanvasModule.ps1`). Enable network access for the session, or approve each
   outbound request when Codex asks — check your installed Codex version's docs for the
   exact current flag/setting name, since this has changed between releases.

Once configured, prompt it the same way: *"Read AGENTS.md and update Module 5 the same
way."*

Codex also has an evolving Skills system (`~/.codex/skills/`) as an alternative
packaging format, but at the time this was written it was still an experimental,
partially-gated feature. `AGENTS.md` is the stable, documented path and is what this
toolkit is built around; revisit Codex's skills system later if you want a tighter
integration.

## Using it with anything else

Any agent that supports the open [AGENTS.md](https://agents.md/) standard (Cursor,
Aider, Windsurf, GitHub Copilot, Gemini CLI, Devin, Factory, and others) will pick this
up the same way Codex does — put `AGENTS.md` somewhere in the tool's instruction search
path. For a plain human with a terminal, `AGENTS.md` is just a procedure to read and
follow by hand; every command in it is copy-pasteable as-is.

---

## Quick start (one module, start to finish)

```bash
# 1. Explore (read-only)
pwsh -NoProfile -File scripts/Get-CanvasModule.ps1 -ModuleId 5106504 -WorkDir ./work/m7

# 2. Read work/m7/bodies/*.html and work/m7/quiz/*.questions.json yourself (or have
#    your agent do it) - this is the judgment step nothing here can script for you.

# 3. Author your changes into new/page.html, new/assignment.html, new/quiz.json

# 4. Validate before anything touches Canvas
python3 scripts/check_style.py new/page.html --palette references/palette.example.txt
python3 scripts/check_quiz.py new/quiz.json --expect-count 14
python3 scripts/diff_content.py work/m7/bodies/01_*.html new/page.html

# 5. Write a changeset (copy examples/changeset.example.json, fill in the IDs from
#    work/m7/ids.json), then dry-run it
pwsh -NoProfile -File scripts/Push-CanvasModule.ps1 -Changeset ./changeset.json

# 6. Read the dry-run plan. If it looks right:
pwsh -NoProfile -File scripts/Push-CanvasModule.ps1 -Changeset ./changeset.json -Apply
```

## Why this is cheap to run

The expensive part of doing this ad hoc, turn after turn, is re-deriving the same
judgments in prose every single time: "does this HTML follow the style rules," "does
this quiz have exactly one right answer per question," "did the rewrite lose any
content." Each of those is a **deterministic check**, not a matter of taste — so it's a
script here, and an agent reads a `PASS`/`FAIL`/`RESULT: N passed, 0 failed` line
instead of reconstructing the reasoning from scratch in every response.

The other big cost driver in ad hoc use is the copy-paste-edit cycle: hand-writing a
new push script for every module by copying the last one and swapping IDs. There is
now exactly **one** push script (`Push-CanvasModule.ps1`), parameterized by a small
JSON changeset — nothing to copy or rewrite per module.

Concretely, this design pushes the AI agent's job down to only the parts that
genuinely need judgment: *what should this content say*, *is this specific named fact
still current*, *does this image need this alt text*. Everything mechanical —
compliance, answer-key integrity, content-preservation, the dry-run/apply plumbing —
runs as code and gets read, not re-derived.

## Safety notes

- **Content-endpoints only.** `lib_canvas.ps1` and every script here only ever touch
  pages, assignments, and quizzes (descriptions + questions). Nothing here can read or
  write rosters, grades, or submissions.
- **Dry-run is the default** on `Push-CanvasModule.ps1`. You must pass `-Apply`
  explicitly to write anything.
- **Quiz question rebuilds are gated** on the course being unpublished, since that's
  the only state that guarantees no student attempts exist yet. Don't bypass this
  unless you've verified independently, through a sanctioned read-only path, that no
  attempts exist.
- **Never commit `canvas.token` or `canvas.config.*.json`** — add them to
  `.gitignore` in whatever repo you drop your working files into.

## Customizing for your institution

1. Copy `references/style-guide.example.md` and rewrite the palette, component names,
   and hex values for your brand. Keep the *structure* (hero / cards / info box /
   footer) and the hard accessibility + Canvas-sanitizer rules — those come from WCAG
   and from what Canvas's editor actually strips, not from any one school's taste.
2. Generate a matching `palette.txt` (one `#rrggbb` per line) and pass it to
   `check_style.py --palette your-palette.txt`.
3. Everything else (the scripts, `AGENTS.md`'s procedure) is already institution-agnostic.
