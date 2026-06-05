# garris-canvas-tools — Canvas tools for instructors (Claude Code)

Two Claude Code plugins for MGCCC instructors. Built and tested on the **MGCCC** Canvas
instance; they reuse cleanly for any Canvas school after a few setting tweaks.

| Plugin | What it does |
|---|---|
| **`notion-to-canvas`** | Builds **Canvas course content** — migrated from a Notion course or generated on request — as styled, sanitizer-safe pages, weekly modules, and (optionally) graded assignments/discussions/quizzes. Idempotent re-runs. **Never reads student data.** |
| **`canvas-pii-guard`** | A **local data-protection layer**: PreToolUse hooks that **block** Canvas student-data API calls (rosters/grades/submissions) and local-cache reads *before they run*, so student PII is never fetched or sent. Plus a best-effort output scrubber tuned to MGCCC ID formats. Install it alongside `notion-to-canvas`. |

> **Not included here:** the admin/grading tool that *intentionally* reads student
> submissions. That stays on admin machines only. Everything in this repo is either
> content-only or PII-protective.

## Requirements
- **Claude Code** (custom skills/plugins are not available in the claude.ai web app or Claude Desktop).
- **PowerShell** (Windows PowerShell 5.1 is fine).
- A **Canvas API access token** for your own account, plus your course base URL + id.
- For Notion-sourced builds only: a connected **Notion MCP** connector.

## Install

### As plugins (recommended)
In Claude Code:
```
/plugin marketplace add billathekilla737/garris-canvas-tools
/plugin install notion-to-canvas@garris-canvas-tools
/plugin install canvas-pii-guard@garris-canvas-tools
```
Reload Claude Code. You'll get a trust prompt on install — review the scripts first.
(Private repo: you must have repo access. If the marketplace command can't authenticate
to a private repo, use the folder-copy method below or clone locally and
`/plugin marketplace add <local path>`.)

### Simplest (copy the folder)
Copy `plugins/notion-to-canvas/skills/notion-to-canvas/` into your `~/.claude/skills/`
so you have `~/.claude/skills/notion-to-canvas/SKILL.md`. Restart Claude Code.
(See `AGENT-INSTALL-PROMPT.md` for a paste-in prompt that does this for you, and
`INSTALL-GUIDE.pdf` for a step-by-step picture guide.)

## First-time setup (each instructor)
1. Generate a token: **Canvas → Account → Settings → New Access Token**. Save it as a
   one-line file `canvas.token` in your project folder. **Never commit it.**
2. Make `canvas.config.<courseId>.json`:
   `{ "base_url": "https://YOURSCHOOL.instructure.com", "course_id": 12345 }`
3. Confirm auth with a cheap read: `GET /api/v1/courses/:id`.

## Usage
Ask Claude in plain English — "get my Notion course into Canvas", "add a study guide to
Week 3", "build a final-exam quiz". Before any push it **asks whether to publish or
leave content unpublished** (default: unpublished). See `plugins/notion-to-canvas/.../SKILL.md`.

---

## Student data security

**Only the course material being built is sent to Claude. Student rosters, grades, and
submissions are blocked by software on the instructor's own computer before anything
could be transmitted — it is not a matter of trusting the AI.**

How it works (defense in depth):
1. **AI policy** — the content skill refuses student-data requests outright (a hard stop).
2. **No risky tools** — the content skill ships *zero* scripts that can read rosters/grades.
3. **Local BLOCK (the guarantee)** — `canvas-pii-guard`'s PreToolUse hook denies any
   Canvas student-data call or local-cache read *before it runs*. Blocked → never fetched
   → nothing to transmit. This is auditable: the rule file is short and readable.
4. **Sterilizing gateway** — if data is ever genuinely needed, raw stays in a private
   folder the agent never reads; only a scrubbed version is emitted.
5. **Output scrubber (backstop)** — best-effort redaction of stray IDs/emails, tuned to
   MGCCC formats (login `M########`, SIS `###.M########`).

**Proven:** a built-in test runs **46 checks** against the exact rules and passes all 46
(and openly lists what it does *not* catch).

**Honest scope:** this is a **proof of concept**, not a literal "air gap" (the machine
still uses the internet). The guarantee is **local prevention** of the student-data path.
The scrubber is a labeled backstop, **not** a 100% claim. To make it an enforced,
institution-wide control, the next steps are a **scoped Canvas token that can't see
grades** and **centrally-enforced settings** users can't disable.

Full documentation:
- [How Student Data Is Secured (PDF, with diagrams)](plugins/canvas-pii-guard/How-Student-Data-Is-Secured.pdf)
- [Security Overview (PDF)](plugins/canvas-pii-guard/Security-Overview.pdf)
- [Data Handling brief (IT/legal)](plugins/canvas-pii-guard/DATA-HANDLING.md)
- [Contingency Analysis (full matrix + hardening roadmap)](plugins/canvas-pii-guard/CONTINGENCY-ANALYSIS.md)
- [Coverage report (test evidence)](plugins/canvas-pii-guard/tests/guard-coverage-report.txt)

To reproduce the evidence: run `plugins/canvas-pii-guard/tests/Run-GuardTests.ps1`.

---

## What to change for a non-MGCCC school
- `plugins/notion-to-canvas/skills/notion-to-canvas/references/style-guide.md` — swap the
  navy/gold palette for your colors (keep the structure; it survives the sanitizer).
- `.../scripts/Trim-CanvasNav.ps1` — the nav keep-list + LTI tab ids are MGCCC's; override `-Keep`.
- `plugins/canvas-pii-guard/scripts/PiiPatterns.ps1` — adjust the ID/email patterns to your
  institution's formats (and re-run the tests).
- `base_url` in your config.

## Notes / safety
- Example course ids in script headers are placeholders (`12345` / `67890`).
- Scripts run with your token against your courses; Claude Code prompts for permission.
- **You** are responsible for FERPA. Don't paste student PII into chats or commits, and
  don't commit `canvas.token`.

## License / origin
Authored by Zack Garris (MGCCC). Share freely with other instructors.
