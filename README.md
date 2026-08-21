# CourseForge — Canvas tools for instructors (Claude Code)

Two Claude Code plugins for MGCCC instructors and curriculum designers, plus a
portable module-update toolkit. Built and tested on the **MGCCC** Canvas instance;
they reuse cleanly for any Canvas school after a few setting tweaks.

| Component | What it does |
|---|---|
| **`courseforge`** | **Remediates, restyles, builds, edits, and moves Canvas courses** — idempotent, dry-run-first on writes: **(1) ADA / Ally remediation of an *existing* course, largely hands-off** — dump → restyle → verify (visible text provably unchanged) → push in place for every HTML body (pages, assignments, discussions, quiz descriptions, syllabus), **plus automated PowerPoint (`.pptx`) alt-text + slide-title and Word (`.docx`) heading/alt/table remediation, and PDF accessibility remediation** — a fast deterministic PDF pipeline (`pdf-fastlane`) that OCRs scanned files, writes real basic tag trees, fixes titles/language, and embeds missing fonts in parallel with no model round-trips, validated against **PDF/UA-1 (ISO 14289-1) via veraPDF** rather than scanner heuristics; only judgment calls (image alt text, ambiguous structure) go to the model through a batched, hash-deduped queue. Measured: 220 course PDFs fixed and verified in ~66 seconds. **(2) Looks overhaul** — restyle a whole course into the branded navy/gold template (clean / rich / hybrid looks). **(3) Generate & place content** — pages, syllabi, assignments, graded discussions, quizzes/exams and study guides, as styled, sanitizer-safe, accessible content placed into the right modules (or seed a new course from a **Notion** export — one input option). **(4) Export / import / clone** — back a course up to a local `.imscc` and import it, or copy one course into another (replica sandboxes). **(5) Document text editing (change requests)** — find-and-fix text *inside* course files: **PDF** scan / in-place edit / form fill (with digital-signature detection and a collision-checked fill preview) and **Office (`.docx`/`.pptx`/`.xlsx`) raw-XML edits** that reach author metadata, slide masters, and footers — e.g. replace a former instructor's name/email/room across every handout, deck, and form in a course. **Content-first: it does not read student data in normal use** and refuses ad-hoc roster/grade/submission access. It adds one **opt-in blind-grading flow** (identities stay local in a gitignored map, the model grades pseudonymized scrubbed text — including opt-in extracted `.docx`/`.pdf`/`.txt` attachment text — and a self-verification gate fails the build if structured PII survives; a dry-run-first poster writes grades back). |
| **`canvas-pii-guard`** | A **local data-protection layer**: PreToolUse hooks that **block** Canvas student-data API calls (rosters/grades/submissions) and local-cache reads *before they run*, so student PII is never fetched or sent. Plus a best-effort output scrubber tuned to MGCCC ID formats. Install it alongside `courseforge`. |
| **`canvas-module-toolkit/`** | A **portable, model-agnostic** module-content updater (restyle to a brand template, refresh content, validate quiz answer keys) that works with **any** agent that can run a shell — Claude Code, OpenAI Codex CLI, or anything speaking the open [AGENTS.md](https://agents.md/) standard. Cross-platform (**PowerShell 7 on macOS/Linux**, 5.1 on Windows); deterministic Python validators (style/palette, quiz keys, content-diff, contrast) so the agent reads checker output instead of re-deriving compliance. See [`canvas-module-toolkit/README.md`](canvas-module-toolkit/README.md). |


## Requirements
- **Claude Code** — for non-technical users the **desktop app** is the recommended
  surface (install it like any program, sign in, no terminal needed day-to-day); the
  CLI and IDE extensions work identically. Custom skills/plugins are not available in
  the claude.ai web app or Claude Desktop (the chat app).
- **PowerShell** (Windows PowerShell 5.1 is fine).
- **Python 3** — powers automated **PPTX / DOCX ADA remediation**, **PDF triage and
  text editing**, **Office document text editing**, blind-grading attachment
  extraction, and the HTML restyle pipeline. Everything else works without it; the
  installer sets up `python-pptx`, `python-docx`, `pypdf`, `PyMuPDF`, `pikepdf` and
  `fontTools` when a real Python is present. Two OPTIONAL extras unlock the rest of
  the PDF pipeline: **Tesseract OCR** (`winget install UB-Mannheim.TesseractOCR`) for
  scanned image-only PDFs, and **veraPDF + a Java runtime** for standards validation
  (`pdf_fastlane.py validate`); without them those steps queue with a clear message
  instead of failing.
  **Windows caveat:** a stock Windows 11 has an App Execution Alias stub at
  `WindowsApps\python.exe` that looks like Python but isn't. The installer detects it and
  says so; install the real thing with
  `winget install --id Python.Python.3.12`, then re-run the install line **in a new
  terminal** (an open shell keeps the stale `PATH`).
- A **Canvas API access token** for your own account, plus your course base URL + id.
- **Optional**, only for the Notion-import build path: a connected **Notion MCP** connector.

## Install — one line

Open **PowerShell** (Start menu → type "PowerShell") and paste:

```powershell
irm https://raw.githubusercontent.com/billathekilla737/CourseForge/main/bootstrap.ps1 | iex
```

That's the whole install: it uses the Claude Code plugin system when the CLI is
available, otherwise downloads this repo and runs the script installer — either way
**both** plugins land (`courseforge` builds courses; `canvas-pii-guard` is the local
block that enforces the no-student-data guarantee), the guard hooks are registered, the
document libraries are set up, and the guard test suite runs. Watch for **ALL TESTS
PASSED**. Safe to re-run any time — re-running is also how you **update**.

Then: **fully restart Claude Code** (approve the trust prompt if one appears), use
**Open Folder** to open `%USERPROFILE%\Documents\canvas-work` (create it if it's new —
it's simply where your Canvas connection gets saved; always open the same folder), and say
*"set up my Canvas."* PowerShell is never needed again after the install line.

> The install prints that folder's absolute path when it finishes. If OneDrive backs up
> your Documents you effectively have two — `%USERPROFILE%\Documents` and
> `%USERPROFILE%\OneDrive\Documents`. Both resolve, but prefer the **local** one: your
> Canvas token is saved there and it carries your full account permissions, so it should
> not sync to the cloud.

**Verify it worked** (after restart) — ask Claude:
> *"Is canvas-pii-guard active, and do you have the courseforge skill?"*

To remove everything later: run [`Uninstall-CourseForge.ps1`](Uninstall-CourseForge.ps1)
(leaves your Canvas tokens/configs alone).

<details>
<summary>Manual alternatives (marketplace commands / script installer)</summary>

**Marketplace, by hand** — in Claude Code, type these three lines (one at a time),
approving the trust prompts:

```
/plugin marketplace add billathekilla737/CourseForge
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

The token is read with `Read-Host -AsSecureString`, so it is typed hidden and **never
enters the chat transcript** — an assistant should launch the setup in its own window
rather than asking you to paste a token into a conversation. Only a masked prefix and a
length are ever echoed back. Already saved it loose as `canvas.token.txt`,
`Canvas Token.txt`, or a pasted `.rtf`? Setup finds it, reuses it, and tidies the stray
file away.

**Consider a scoped token.** For content-only work (remediation, restyling, building
pages/quizzes), a Canvas role *without* view-grades / view-students makes student PII
unfetchable at the source — the strongest version of the guarantee below, since it doesn't
depend on hooks at all.

**By hand (if you prefer):**
1. Generate a token: **Canvas → Account → Settings → New Access Token**. Save it as a
   one-line file `canvas.token` in your project folder. **Never commit it.**
2. Make `canvas.config.<courseId>.json`:
   `{ "base_url": "https://YOURSCHOOL.instructure.com", "course_id": 12345 }`
3. Confirm auth with a cheap read: `GET /api/v1/courses/:id`.

## Usage
Ask Claude in plain English:
- **ADA compliance (existing course):** *"bring this course up to ADA compliance"*, *"fix my Ally score"*, *"make these PowerPoints accessible"*, *"which of my PDFs hurt the score?"*, *"fix every PDF in this course and prove it against PDF/UA-1"*
- **Looks overhaul:** *"give this course the school look"*, *"restyle Week 1 in the navy template"*
- **Generate & place content:** *"add a study guide to Week 3"*, *"build a final-exam quiz"*, *"write a syllabus page", *"Post an announcement"*, *"Create a discussion"*.*
- **Document text fixes (change requests):** *"find every mention of the previous instructor in this course and replace it with my info — including inside the PowerPoints, Word docs, and PDFs"*, *"fill out this PDF form"*, *"fix the dates in these handouts"*
- **Backup / copy:** *"export this course as a backup"*, *"clone this course into a sandbox"*
- **Blind grading (opt-in):** *"pull the submissions for this assignment and let's grade them anonymously"*
- **Course Rollover:** *"Roll these course due dates over to next semester. Look at the academic calendar and schedule due dates around breaks."*
- **Contradiction sweep:** *"Check and see if any of my course polices contradict each other."*
- **Course Calendar Creation:** *"Generate a Course calendar graphic for the semester and place it in the top module."*
- **SLO Alignment:** *"Show me which assignments align with each learning outcome for my course?"*
- **Build from Notion (optional):** *"get my Notion course into Canvas"*

## TODO
Request Features:
- **Audio and Video Transcription using Whisper AI"*
  
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
5. **Opt-in blind grading** — `Build-GradingBundle.ps1` pulls submission **text**,
   keeps the pseudonym→identity `map.json` **local** (gitignored, never read by the
   model), and emits a scrubbed, pseudonymized `bundle.json`: structured PII first
   (emails, phones, MGCCC ids, **bare 8–10 digit runs**), then **every roster
   student's full-name forms** (peer mentions included) plus the author's own name
   tokens. The finished bundle is **re-verified** — with the guard's independent
   redactor — and the build **fails** if structured PII survives. Optionally
   (`-IncludeAttachmentText`) `.docx`/`.pdf`/`.txt` attachment text is extracted
   locally and scrubbed through the same pipeline, making file-upload assignments
   gradeable; **images are never inlined** (screenshots carry names in title bars —
   review those locally). `Post-Grades.ps1` posts grades back by pseudonym,
   **dry-run first**, audited. The model grades `S-001`, `S-002`, …, never names.
   Still **best-effort de-identification, not a guarantee** — identifying free-text
   content can survive any scrubber.
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
