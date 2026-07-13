# garris-canvas-tools — Canvas tools for instructors (Claude Code)

Two Claude Code plugins for MGCCC instructors and curriculum designers. Built and tested
on the **MGCCC** Canvas instance; they reuse cleanly for any Canvas school after a few
setting tweaks.

| Plugin | What it does |
|---|---|
| **`courseforge`** | **Remediates, restyles, builds, and moves Canvas courses** — idempotent, dry-run-first on writes: **(1) ADA / Ally remediation of an *existing* course, largely hands-off** — dump → restyle → verify (visible text provably unchanged) → push in place for every HTML body (pages, assignments, discussions, quiz descriptions, syllabus), **plus automated PowerPoint (`.pptx`) alt-text + slide-title remediation**. **(2) Looks overhaul** — restyle a whole course into the branded navy/gold template (clean / rich / hybrid looks). **(3) Generate & place content** — pages, syllabi, assignments, graded discussions, quizzes/exams and study guides, as styled, sanitizer-safe, accessible content placed into the right modules (or seed a new course from a **Notion** export — one input option). **(4) Export / import / clone** — back a course up to a local `.imscc` and import it, or copy one course into another (replica sandboxes). **Content-first: it does not read student data in normal use** and refuses ad-hoc roster/grade/submission access. It adds one **opt-in blind-grading flow** (a sterilizing + pseudonymizing gateway: identities stay local, you grade pseudonymized text, a dry-run-first poster writes grades back). |
| **`canvas-pii-guard`** | A **local data-protection layer**: PreToolUse hooks that **block** Canvas student-data API calls (rosters/grades/submissions) and local-cache reads *before they run*, so student PII is never fetched or sent. Plus a best-effort output scrubber tuned to MGCCC ID formats. Install it alongside `courseforge`. |

> **Not included here:** the full admin/grading tool that *intentionally* reads student
> submissions **with real identities**. That stays on admin machines only. What this
> repo's content plugin *does* include is an **opt-in blind-grading** path that keeps
> identities local and only shows the model pseudonymized, scrubbed submission text
> (best-effort de-identification, not a guarantee — see Student data security below).

## Requirements
- **Claude Code** — for non-technical users the **desktop app** is the recommended
  surface (install it like any program, sign in, no terminal needed day-to-day); the
  CLI and IDE extensions work identically. Custom skills/plugins are not available in
  the claude.ai web app or Claude Desktop (the chat app).
- **PowerShell** (Windows PowerShell 5.1 is fine).
- **Python 3** — powers the automated **PPTX ADA remediation** and the HTML restyle
  pipeline. Everything else works without it; the installer sets up `python-pptx` for
  you when Python is present.
- A **Canvas API access token** for your own account, plus your course base URL + id.
- **Optional**, only for the Notion-import build path: a connected **Notion MCP** connector.

## Install — one line

Open **PowerShell** (Start menu → type "PowerShell") and paste:

```powershell
irm https://raw.githubusercontent.com/billathekilla737/garris-canvas-tools/main/bootstrap.ps1 | iex
```

That's the whole install: it uses the Claude Code plugin system when the CLI is
available, otherwise downloads this repo and runs the script installer — either way
**both** plugins land (`courseforge` builds courses; `canvas-pii-guard` is the local
block that enforces the no-student-data guarantee), the guard hooks are registered,
`python-pptx` is set up, and the guard test suite runs. Safe to re-run any time —
re-running is also how you **update**.

Then: **fully restart Claude Code** (approve the trust prompt if one appears), use
**Open Folder** to open `Documents\canvas-work` (create it if it's new — it's simply
where your Canvas connection gets saved; always open the same folder), and say
*"set up my Canvas."* PowerShell is never needed again after the install line.

**Verify it worked** (after restart) — ask Claude:
> *"Is canvas-pii-guard active, and do you have the courseforge skill?"*

To remove everything later: run [`Uninstall-CourseForge.ps1`](Uninstall-CourseForge.ps1)
(leaves your Canvas tokens/configs alone).

<details>
<summary>Manual alternatives (marketplace commands / script installer)</summary>

**Marketplace, by hand** — in Claude Code, type these three lines (one at a time),
approving the trust prompts:

```
/plugin marketplace add billathekilla737/garris-canvas-tools
/plugin install courseforge@garris-canvas-tools
/plugin install canvas-pii-guard@garris-canvas-tools
```

**Script installer, by hand** (no git needed — download the repo ZIP from GitHub,
extract, then):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-CourseForge.ps1
```

It merges the guard hooks into any existing `settings.json` (writing a `.bak` first)
and runs the full guard test suite at the end. An AI assistant can drive this for
you — see [`AGENT-INSTALL-PROMPT.md`](AGENT-INSTALL-PROMPT.md).

> ⚠️ Do **not** hand-copy just the skill folder into `~/.claude/skills/` — that installs
> only the content skill and leaves the safety hooks uninstalled. Every supported path
> above registers the hooks.
</details>

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
Ask Claude in plain English:
- **ADA compliance (existing course):** *"bring this course up to ADA compliance"*, *"fix my Ally score"*, *"make these PowerPoints accessible"*
- **Looks overhaul:** *"give this course the school look"*, *"restyle Week 1 in the navy template"*
- **Generate & place content:** *"add a study guide to Week 3"*, *"build a final-exam quiz"*, *"write a syllabus page"*
- **Backup / copy:** *"export this course as a backup"*, *"clone this course into a sandbox"*
- **Build from Notion (optional):** *"get my Notion course into Canvas"*

Before any push it **asks whether to publish or leave content unpublished** (default:
unpublished), and content writes are **dry-run-first**. Remediation **never changes your
modules or publish state** and preserves instructional text verbatim. See
`plugins/courseforge/.../SKILL.md` and `references/ada-remediation.md`.

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

**Proven:** a built-in test suite runs dozens of checks against the exact rules and passes all of them
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
