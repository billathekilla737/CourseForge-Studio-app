# garris-canvas-tools — Canvas tools for instructors (Claude Code)

Two Claude Code plugins for MGCCC instructors. Built and tested on the **MGCCC** Canvas
instance; they reuse cleanly for any Canvas school after a few setting tweaks.

| Plugin | What it does |
|---|---|
| **`courseforge`** | Builds **Canvas course content** — migrated from a Notion course or generated on request — as styled, sanitizer-safe pages, weekly modules, and (optionally) graded assignments/discussions/quizzes. Idempotent re-runs. **Content-first: it does not read student data in normal use** and refuses ad-hoc roster/grade/submission access. It adds one **opt-in blind-grading flow** (a sterilizing + pseudonymizing gateway: identities stay local, you grade pseudonymized text, a dry-run-first poster writes grades back). |
| **`canvas-pii-guard`** | A **local data-protection layer**: PreToolUse hooks that **block** Canvas student-data API calls (rosters/grades/submissions) and local-cache reads *before they run*, so student PII is never fetched or sent. Plus a best-effort output scrubber tuned to MGCCC ID formats. Install it alongside `courseforge`. |

> **Not included here:** the full admin/grading tool that *intentionally* reads student
> submissions **with real identities**. That stays on admin machines only. What this
> repo's content plugin *does* include is an **opt-in blind-grading** path that keeps
> identities local and only shows the model pseudonymized, scrubbed submission text
> (best-effort de-identification, not a guarantee — see Student data security below).

## Requirements
- **Claude Code** (custom skills/plugins are not available in the claude.ai web app or Claude Desktop).
- **PowerShell** (Windows PowerShell 5.1 is fine).
- A **Canvas API access token** for your own account, plus your course base URL + id.
- For Notion-sourced builds only: a connected **Notion MCP** connector.

## Install

Install **both** plugins from the marketplace. The safety layer (`canvas-pii-guard`) is a
**hooks** plugin — it must be *registered* with Claude Code, which a manual folder copy
cannot do. In Claude Code, type these three lines (one at a time):

```
/plugin marketplace add billathekilla737/garris-canvas-tools
/plugin install courseforge@garris-canvas-tools
/plugin install canvas-pii-guard@garris-canvas-tools
```

You'll get a **trust prompt** on install — review the scripts, then approve. **Fully
restart Claude Code** so the skills and hooks load.

> **Install both.** `courseforge` builds your courses; `canvas-pii-guard` is the local
> block that actually enforces the no-student-data guarantee. Installing courseforge
> alone leaves that protection **off**.

**Verify it worked** (after restart) — ask Claude:
> *"Is canvas-pii-guard active, and do you have the courseforge skill?"*

It should confirm the courseforge skill is available **and** that the PII-guard
PreToolUse hook is registered. If the guard isn't active, re-run the third command.

### If `/plugin` isn't available (SDK harness, automation)

Some surfaces — the **Claude Agent SDK** harness, headless/automation runs — can't load
the plugin marketplace and will say `/plugin` *"isn't available in this environment."*
For those, run the bundled installer instead. Crucially, it does what a bare folder copy
could not: it **registers the canvas-pii-guard hooks** into `settings.json`, so the
safety block is never left off. It's idempotent, merges into any existing `settings.json`
(writing a `.bak` first), and runs the 51-check guard suite at the end.

```powershell
git clone https://github.com/billathekilla737/garris-canvas-tools "$env:TEMP\garris-canvas-tools"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\garris-canvas-tools\Install-CourseForge.ps1"
```

An AI assistant can run those two lines for you (they're ordinary commands, not slash
commands). See [`AGENT-INSTALL-PROMPT.md`](AGENT-INSTALL-PROMPT.md) for a paste-ready
prompt.

> ⚠️ Do **not** hand-copy just the skill folder into `~/.claude/skills/` — that installs
> only the content skill and leaves the safety hooks uninstalled. Use the marketplace
> commands, or `Install-CourseForge.ps1`, both of which register the hooks.

## First-time setup (each instructor)

**The easy way (recommended):** in your course folder, ask Claude to *"set up my
Canvas"* (or run `scripts\Setup-Canvas.ps1`). It asks two questions — your **course
web address** and your **access token** (typed hidden) — and does everything else:
saves the token correctly, writes the config, protects it with `.gitignore`, and
tests the connection, printing your course name when it works. No file paths, no
file-extension headaches. To get the token: **Canvas → Account → Settings → New
Access Token**, then paste it when asked.

**By hand (if you prefer):**
1. Generate a token: **Canvas → Account → Settings → New Access Token**. Save it as a
   one-line file `canvas.token` in your project folder. **Never commit it.**
2. Make `canvas.config.<courseId>.json`:
   `{ "base_url": "https://YOURSCHOOL.instructure.com", "course_id": 12345 }`
3. Confirm auth with a cheap read: `GET /api/v1/courses/:id`.

## Usage
Ask Claude in plain English — "get my Notion course into Canvas", "add a study guide to
Week 3", "build a final-exam quiz". Before any push it **asks whether to publish or
leave content unpublished** (default: unpublished). See `plugins/courseforge/.../SKILL.md`.

---

## Student data security

**In normal content work, only the course material being built is sent to Claude.
Ad-hoc student rosters, grades, and submissions are blocked by software on the
instructor's own computer before anything could be transmitted — it is not a matter of
trusting the AI. There is one opt-in exception, described below, that is designed to
keep identities local even while grading.**

How it works (defense in depth):
1. **AI policy** — in normal use the content skill refuses ad-hoc student-data requests.
2. **Few risky tools, all sanctioned** — the only scripts that touch student data are the
   two named gateways below; there is no general-purpose roster/grade reader to misuse.
3. **Local BLOCK (the guarantee)** — `canvas-pii-guard`'s PreToolUse hook denies any
   Canvas student-data call or local-cache read *before it runs*. Blocked → never fetched
   → nothing to transmit. It recognizes the sanctioned gateways by name and still blocks
   everything else fail-closed. This is auditable: the rule file is short and readable.
4. **Sterilizing gateway** — if non-grading data is ever genuinely needed,
   `Get-CanvasData-Sterilized.ps1` keeps the raw response in a private folder the agent
   never reads and emits only a scrubbed version.
5. **Opt-in blind grading** — `Build-GradingBundle.ps1` pulls submission **text only**,
   keeps the pseudonym→identity `map.json` **local** (gitignored, never read by the
   model), and emits a scrubbed, pseudonymized `bundle.json`; `Post-Grades.ps1` posts
   grades back by pseudonym, **dry-run first**, audited. The model grades `S-001`,
   `S-002`, …, never names. This is **best-effort de-identification, not a guarantee** —
   free-text PII and names embedded in screenshots/uploaded files can remain (file
   contents are never downloaded — only filenames are listed for local review).
6. **Output scrubber (backstop)** — best-effort redaction of stray IDs/emails, tuned to
   MGCCC formats (login `M########`, SIS `###.M########`).

**Proven:** a built-in test runs **51 checks** against the exact rules and passes all 51
(including that the two grading gateways are allowed at `/submissions` and to read their
local map, while generic commands hitting `/submissions` or `grading/` are still denied)
— and it openly lists what it does *not* catch.

**Honest scope:** this is a **proof of concept**, not a literal "air gap" (the machine
still uses the internet). The defensible guarantee is **local prevention** of the
unsanctioned student-data path; the grading gateway adds **best-effort** local-only
de-identification, **not** a 100% claim. To make it an enforced, institution-wide
control, the next steps are a **scoped Canvas token that can't see grades** and
**centrally-enforced settings** users can't disable.

Full documentation:
- [How Student Data Is Secured (PDF, with diagrams)](plugins/canvas-pii-guard/How-Student-Data-Is-Secured.pdf)
- [Security Overview (PDF)](plugins/canvas-pii-guard/Security-Overview.pdf)
- [Data Handling brief (IT/legal)](plugins/canvas-pii-guard/DATA-HANDLING.md)
- [Contingency Analysis (full matrix + hardening roadmap)](plugins/canvas-pii-guard/CONTINGENCY-ANALYSIS.md)
- [Coverage report (test evidence)](plugins/canvas-pii-guard/tests/guard-coverage-report.txt)

To reproduce the evidence: run `plugins/canvas-pii-guard/tests/Run-GuardTests.ps1`.

---

## What to change for a non-MGCCC school
- `plugins/courseforge/skills/courseforge/references/style-guide.md` — swap the
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
