---
name: courseforge
description: >-
  Remediate, restyle, build, and move content on Canvas LMS — styled, accessible,
  idempotent, dry-run-first. Main jobs: (1) bring an EXISTING course up to
  ADA/Ally accessibility with little or no manual oversight — fix HTML pages,
  assignments, discussions, quiz descriptions and the syllabus (dump → restyle →
  verify visible text unchanged → push in place, never touching modules or publish
  state) AND remediate PowerPoint (.pptx) decks (descriptive alt text + slide
  titles); (2) give a course a looks overhaul — restyle it into the branded template
  (clean / rich / hybrid); (3) generate content on request — a page, syllabus,
  assignment, graded discussion, quiz or final exam, study guide — placed into the
  right module, item type, position, points and assignment group; (4)
  export / import / clone whole courses — .imscc backups, cartridge import, and
  Canvas-to-Canvas copy for replica sandboxes; (5) build a course from a source,
  including the OPTIONAL path of migrating a Notion course. Use whenever the user
  wants to make a course ADA / accessible / Ally-compliant, fix alt text or headings,
  remediate PowerPoints, restyle or "give my course the school look", add or build
  pages / assignments / quizzes / exams / study guides, export / back up / copy /
  clone a course, populate an empty Canvas shell, or "get my Notion course into
  Canvas" — even if they never say "Canvas API" or name this skill. Also covers
  standalone sub-tasks: trimming a course's left-hand nav, and bulk-converting many
  pages with a parallel workflow. (6) **SLO ALIGNMENT** — cross-reference a Canvas shell
  against the state Student Learning Outcomes for its program and report which
  assignments satisfy which outcomes, where the gaps are, and how to close them. Use
  whenever an instructor in ANY program (automotive, networking, cyber security, cloud,
  welding, business, health sciences, English, ...) asks whether their course or program
  is "aligned", asks about SLOs / student learning outcomes / curriculum framework /
  program review / accreditation evidence, or asks which of their assignments meet their
  program's outcomes. Before any push, ask whether the work should be
  published or left unpublished. This skill is content-first and does not read student
  data in normal use; it refuses ad-hoc roster/grade/submission access. It DOES include
  one OPT-IN blind-grading flow: a sterilizing + pseudonymizing gateway pulls submission
  TEXT only (identities stay local), you grade by pseudonym, and a dry-run-first poster
  writes the grades back. That de-identification is best-effort, not a guarantee. For
  full admin grading with real identities, use the courseforge-admin skill. Also
  handles FIRST-TIME CONNECTION: when Canvas is not set up yet (no token/config) and the
  user asks to set up or connect Canvas, to "see", "list", or "show" their courses,
  shells, or sections, or wants any Canvas action at all, proactively run the
  Setup-Canvas onboarding (it collects the course URL and a privately-typed token)
  instead of replying that you have no access. Built and battle-tested on the MGCCC
  Canvas instance; the conventions reuse cleanly for any Canvas school.
compatibility: Requires PowerShell and a Canvas API token. Python 3 (with python-pptx, python-docx, pypdf) powers PowerPoint/Word remediation, PDF triage, and the HTML restyle pipeline. The Notion MCP connector is OPTIONAL — needed only for the Notion-import build path (Mode A).
---

# CourseForge — Canvas course toolkit

Point this at a Canvas course to **remediate, restyle, build, or move** it. The jobs,
most-used first:

1. **Remediate for accessibility (ADA / Ally).** Bring an *existing* course up to
   compliance with little or no manual oversight — every HTML body (pages, assignments,
   discussions, quiz descriptions, the syllabus) **and PowerPoint (`.pptx`) decks**. The
   scripted pipeline (dump → restyle → verify text-unchanged → push in place, never
   touching modules or publish state) and the PPTX gateway both live in
   `references/ada-remediation.md`.
2. **Restyle / looks overhaul.** Reskin a course into the branded template — clean,
   rich, or hybrid looks (`references/style-guide.md`).
3. **Generate & place content.** Create a page, syllabus, assignment, graded
   discussion, quiz/exam or study guide and place it into the right module, item type,
   position, points and assignment group (**Mode B** below).
4. **Export / import / clone.** Back a course up to a local `.imscc`, import a
   cartridge, or copy one course into another for a replica sandbox
   (`Export-CanvasCourse.ps1` / `Import-CanvasCourse.ps1`).
5. **Build a course from a source.** Populate a shell — including the *optional* path
   of migrating a **Notion** course (**Mode A** below; one input option, not the main
   event).

Two rules cut across all of it: every HTML output follows `references/style-guide.md`
(that is what survives the Canvas sanitizer and stays consistent), and writes are
**idempotent** and **dry-run-first** — safe to re-run to update in place.

## STEP 0 — Connect Canvas if it isn't yet (do this BEFORE any Canvas action)
On a fresh install the instructor has the skill but has **not connected their Canvas
yet** — there is no token and no config. Any read or build will fail with "no access."
**Do not just tell the user you can't see Canvas. Offer to connect it now, then run
setup.** This is the #1 onboarding moment for a non-technical instructor — own it.

**Detect it:** look for BOTH a `canvas.token` file AND a `canvas.config.<id>.json`,
checking (a) the current working directory, then (b) the **standard folder**
`Documents\canvas-work` (where the default setup writes them — always check here if the
current dir has no token). **Check BOTH `%USERPROFILE%\Documents\canvas-work` and the
shell's redirected Documents** (`[Environment]::GetFolderPath('MyDocuments')`, e.g.
`%USERPROFILE%\OneDrive\Documents\canvas-work`): with OneDrive Known Folder Move on —
common on school-managed laptops — those are two different folders, and the one the
instructor sees in File Explorer is the redirected one. Prefer the **local**
(non-OneDrive) path when creating it, so the token is not synced to cloud storage
(see Gotcha 11). If you find them in `canvas-work`, use that
folder's `canvas.token` / `canvas.config.*.json` for all script `-ConfigPath`/`-TokenPath`
arguments (the `CanvasContext.ps1` resolver in every push/dump script does this
automatically). If you are unsure which folder is the project, ask the user. If either
file is missing everywhere, they are **not connected** — proactively run onboarding
instead of declining. (Designers working MANY courses: one folder per course, config +
token side by side; disambiguate with `-CourseId`.)

**Run setup yourself — this is the easiest path for the user:**

**Preferred — token stays private (never enters the chat).** Launch the interactive
setup in its own console window so the token is typed hidden, like a password box:
```powershell
Start-Process powershell -ArgumentList @(
  '-NoExit','-ExecutionPolicy','Bypass',
  '-File', "$HOME\.claude\skills\courseforge\scripts\Setup-Canvas.ps1",
  '-WorkingDir', '<the project folder>'
)
```
Then tell the user: *"A small setup window just opened — paste your Canvas course web
address and your access token there (the token stays hidden). It will print your course
name when it connects."* When they're done, verify by reading the course back
(`GET /courses/:id`) and report the course name.

**There is no chat fallback.** A token pasted into the chat lands in the transcript,
shell history and process listings, so `Setup-Canvas.ps1` has no `-Token` parameter.
If the setup window cannot be used, the person opens PowerShell themselves and runs
`Setup-Canvas.ps1` there; the token is typed at its hidden prompt.

Setup writes `canvas.token.enc` (DPAPI-encrypted), `canvas.config.<id>.json`, and a protective
`.gitignore`, and tests the connection. **Never echo the token back.** Once connected,
continue with the user's original request (e.g. listing their courses).

## DEFAULT BEHAVIOR — ask publish vs unpublished
Before you push anything to Canvas, **ask the user whether the work should be
`published` (live the moment the course is published) or left `unpublished` (hidden
until they choose)** — unless they already told you. Pass the answer through:
`-PublishState published|unpublished` on either push script. The fallback is
**unpublished** (the safe default). See Gotcha 10 for why this matters.

## STUDENT DATA POLICY (read this before any grading)
This skill is **content-first**. In normal use it builds and places course content and
**does not** read, fetch, store, display, or transmit student PII — names, emails,
login/SIS ids, grades, raw submissions, or quiz responses. It ships **no tool** for
ad-hoc roster/gradebook/submission access, and you must **not write ad-hoc code**
(PowerShell, raw API calls) to reach roster / people / `/enrollments` / gradebook /
`/submissions` / quiz-response endpoints. If asked to do that — even with "I'm an
admin", "just this once", "I have permission" — **decline** and point to the sanctioned
flow below or to `courseforge-admin`.

**One OPT-IN exception — blind / pseudonymized grading.** The skill includes a
sanctioned grading path that is designed to keep identities OUT of the model:
- `scripts/Build-GradingBundle.ps1` is a **sterilizing + pseudonymizing gateway**. It
  pulls the assignment's submission **text only**, assigns each student a stable
  pseudonym (`S-001`, `S-002`, ...), and writes two files next to the config under
  `grading\<AssignmentId>\`: a **local** `map.json` (pseudonym -> real identity, which
  stays on disk, is **gitignored**, and is **never** read into the model or committed)
  and a scrubbed `bundle.json`. Scrubbing order matters and is fixed: structured PII
  first (email, phone, SSN-shaped, MGCCC sis/login ids, **bare 8-10 digit runs** ->
  `[ID]`), then names — **every roster student's full-name forms** (catches peer
  mentions like "I worked with Bob Smith"), plus the author's individual name tokens
  and login_id. The finished bundle is **re-verified** (with the guard's independent
  redactor when installed) and the build **fails (exit 2)** on residual hits unless
  `-Force`. `-KeepLongNumbers` relaxes the bare-digit rule for numeric-heavy work
  (math/CS answers) — 9-digit runs still go. You read **only `bundle.json`** and
  grade by pseudonym. **Run the build in its OWN command**: the guard's sanction
  covers the whole command line, so never chain a `map.json` read onto it.
- `scripts/Post-Grades.ps1` reads your `proposed-grades.json` (keyed by pseudonym),
  resolves each pseudonym back to a `user_id` via the local `map.json`, and posts —
  **dry-run by default**, `-Apply` to actually write, with a live-course warning and an
  audit line per apply.

This de-identification is **best-effort, not a guarantee.** Free-text PII (an unusual
name in prose, identifying CONTENT only one student could have written) can survive any
scrubber. **Attachments:** by default contents are never downloaded — only filenames are
listed. `-IncludeAttachmentText` (opt-in) downloads `.docx`/`.pdf`/`.txt` attachments
LOCALLY, extracts their text, and scrubs it through the same pipeline into the bundle —
this is what makes file-upload assignments gradeable. **Images and every other type stay
filename-only on purpose**: screenshots carry names in Windows title bars, email headers,
and signatures, and no text scrubber sees pixels — review those **locally**, never send
them to the model. The full workflow and rules are in `references/blind-grading.md`. For
grading that needs real identities in front of the model, use `courseforge-admin` instead.

**Allowed (the bulk of the job, *not* PII):** reading and writing course *content* —
pages, modules, assignments, quizzes, syllabus, files, config — and aggregate counts
that carry no identifiers (e.g. `total_students`). Also allowed: **listing the
instructor's own courses / shells** (`GET /api/v1/courses`) and reading course metadata
(name, dates, term, workflow_state) — that is the instructor's own catalog, not student
data, so do it freely when asked to "see"/"list"/"show" their courses.

Never write student PII into transcripts, logs, manifests, or committed files.

Honest scope (so you don't misrepresent it): the rule above is an instruction-level
guardrail. The **`canvas-pii-guard`** component enforces it locally — a PreToolUse hook
that blocks student-data API calls and local-cache reads **before** they run
(fail-closed), and it now recognizes `Build-GradingBundle.ps1` and `Post-Grades.ps1` as
**sanctioned gateways** while still blocking every other student-data access. Pair it
with a **scoped Canvas token** (a role without view-grades / view-students permissions)
for the real enforcement. Don't claim it is an air gap or unbreakable; the protections
are prevention (block hook + no ad-hoc tools) plus best-effort de-identification.

## Mode A — Build a course from a Notion source (one input path)

Use this only when the user actually has a Notion course to import. It is one way to
*populate* a course; the accessibility, restyle, generate, and export/import jobs above
do not involve Notion at all.

```
Notion hub page (Page Index + weekly schedule)
   -> one styled, Canvas-safe HTML file per page   (the conversion step)
   -> a manifest mapping each file to its week-module + Learn/Build slot
   -> VERIFY each file landed in the right slot     (non-negotiable — see Gotcha 1)
   -> push: create/update pages, build modules, place items  (Push-CanvasPages.ps1)
   -> trim the course nav                           (Trim-CanvasNav.ps1)
```

Everything lives under a project working directory (the user already has one, or
make one). Layout used by the scripts:

```
<project>/
  canvas.token                     # the API token, one line (keep private)
  canvas.config.<courseId>.json    # { base_url, course_id, course_label }
  canvas-export/
    pages/<COURSE>/<id>.html       # one converted page per Notion page
    manifest.<courseId>.json       # slot mapping (see below)
  canvas.state.<courseId>.json     # written by the uploader; enables idempotency
```

## Before you start (prerequisites)

**Easiest path — one command does all the token/config setup.** For a
non-technical instructor, do NOT make them find folders, copy paths, or hand-create
a `.token` file (Windows hides extensions, so they end up with `canvas.token.txt`).
Instead run the onboarding script from their working directory:

```powershell
scripts\Setup-Canvas.ps1
```

It asks just two things — their **course web address** and their **access token**
(typed hidden) — then writes `canvas.token` and `canvas.config.<id>.json` correctly,
adds a `.gitignore` so the token can't be pushed, and **tests the connection**,
printing the course name on success. It is forgiving: if they already saved the
token as `canvas.token.txt`, a `Canvas Token.txt`, or a pasted `.rtf` in that folder,
it finds it, fixes it, and reuses it instead of asking. You can also drive it
non-interactively: `Setup-Canvas.ps1 -CourseUrl <url> -Token <tok>`. Never echo the
token back. **Prefer this over the manual steps below.**

If you must set up by hand instead:

1. **Canvas API token.** The user generates one at *Canvas → Account → Settings →
   New Access Token*. If they pasted it into an `.rtf`, run
   `scripts/Extract-CanvasToken.ps1` to pull it into `canvas.token` (RTF splits
   the token across formatting runs; the script rejoins it). Otherwise just have
   them save it as plain text in `canvas.token`. Never echo the token back.
2. **Course id + base url.** From the course URL `https://SCHOOL.instructure.com/
   courses/12345` → base_url `https://SCHOOL.instructure.com`, course_id `12345`.
   Write `canvas.config.<id>.json`. Prefer an **unpublished** shell first.
3. Sanity-check auth with a cheap read before writing anything:
   `GET /api/v1/courses/:id` (expect the course name back).
4. **Only for a Notion import (Mode A):** the **Notion MCP connector** must be
   connected (you'll use its `fetch` and `search`). Not needed for any other job.

## Step 1 — Map the Notion course

Fetch the hub page. It has a **Page Index** (the canonical list of pages, each with
its real title) and a **weekly schedule** (which page is the Learn/Read vs the
Build/Do/Assess for each week). Build a work-list: for every page, record
`notion_id, title, module ("Week N — Theme"), module_position, item_type, position`.

Trust the **week themes** for placement, but be skeptical of the schedule's
page→week links (they can be miswired). The titles in the Page Index are reliable;
verification in Step 4 is what actually guarantees correctness.

Pages the schedule references but that live in *other* Notion courses (shared
projects, quizzes) are **gaps** — don't fabricate them. List them for the user.

## Step 2 — Convert each page to Canvas-safe HTML

Read `references/style-guide.md` (the component library + hard rules) and
`references/conversion-spec.md` (the exact per-page conversion instructions).
Write one `<id>.html` per page into `canvas-export/pages/<COURSE>/`.

The output is a single inline-styled `<div>` (no `<html>`/`<head>`). It must
survive the Canvas sanitizer: **inline styles only; no tables, `<script>`,
`<style>`, `class=`, nested lists, `<ol>`, `<br>`, or `box-shadow`** (see Gotcha 3).
Apply the humanizer lightly (Gotcha 4).

**For more than ~10 pages, fan out a parallel workflow** — one agent per page —
instead of converting serially. See `references/workflow-pattern.md`. It's
dramatically faster (51 pages in ~7 minutes vs. a long serial slog).

## Step 3 — Write the manifest

`canvas-export/manifest.<id>.json`:
```json
{
  "course_label": "IMT 1213 — Game Theory and Mechanics",
  "pages": [
    { "notion_id": "abc…", "file": "canvas-export/pages/IMT1213/abc….html",
      "title": "Course Syllabus", "module": "Start Here",
      "module_position": 1, "item_type": "Info", "position": 1 },
    { "notion_id": "def…", "file": "…/def….html",
      "title": "What Is a Game?", "module": "Week 1 — What Is a Game?",
      "module_position": 2, "item_type": "Read", "position": 1 }
  ]
}
```
`module_position` orders the modules (use a "Start Here" at 1, then weeks at 2..14
so the syllabus sits on top). `position` orders items inside a module (Learn=1,
Build=2, Assess=3). The Canvas page title comes from `title`; the page body from
`file`.

## Step 4 — VERIFY before pushing (do not skip)

```powershell
scripts\Verify-Slots.ps1 -Root "<project>" -ManifestPaths .\canvas-export\manifest.<id>.json
```
This compares every file's rendered `<h2>` hero (the page actually fetched)
against its slot title. **Exit code 0 = safe to push.** Any mismatch means
content scrambled (Gotcha 1) — fix first.

## Step 5 — Push

```powershell
scripts\Push-CanvasPages.ps1 `
  -ConfigPath .\canvas.config.<id>.json `
  -ManifestPath .\canvas-export\manifest.<id>.json `
  -PublishState unpublished   # ASK the user first (Gotcha 10); default unpublished
  # ...and -Apply to write. Without it this is a DRY RUN that prints the plan.
```
Creates/updates each page, builds the modules, and places items in order. It's
idempotent via the state file (`canvas.state.<id>.json` beside the config, one per
course) — re-run any time to push edits without duplicating. **Dry run by default;
pass `-Apply` to write.**

## Step 6 — Trim the nav

```powershell
scripts\Trim-CanvasNav.ps1            # dry run: shows what would be hidden/ordered
scripts\Trim-CanvasNav.ps1 -Apply     # trims the connected course's navigation
```
Hides the institutional bloat, leaving a clean keep-list. It targets the connected
course on its own Canvas site (a `-BaseUrl` naming any other host is refused). Override `-Keep` for a
different school/layout (find tab ids via `GET /courses/:id/tabs`).

## Step 7 — Verify & report

`GET /courses/:id/pages` and `/modules?include[]=items` to confirm counts. Report
the module/item structure, the **cross-course gaps** from Step 1, and any
syllabus values worth confirming (credit hours, CRN) before the user publishes.

## Mode B — Generate & place content (not from Notion)

You will often be asked to *create* content and put it on Canvas ("write a final
exam and add it", "make a Week 14 study guide", "add a rubric page to Week 3"). The
**placement** is the skilled part; the steps:

1. **Author** the content as Canvas-safe HTML following `references/style-guide.md`
   (one outer `<div>`, hero `<h2>`, `<h3>` section cards, the navy/gold components).
   Humanize prose (Gotcha 4). Write each file under `canvas-export/pages/<COURSE>/`.
   For a batch (e.g. a study guide + exam per course), fan out subagents.
   **Hints are not solutions:** never put a complete, copy-paste-able solution (full code or a finished
   worked example) in an assignment's hints OR requirements. Give a *skeleton* (class + method
   signatures with `// TODO:` where the graded logic goes), *name the APIs* the student needs without
   assembling them, and use at most one sparing `____` fill-in-the-blank. The student must still write
   every line the rubric grades. Litmus test: if pasting the hint earns the rubric, it gives away too much.
   **Accessibility (every page — see `references/style-guide.md` for the full checklist + both looks):**
   real `<h2>`/`<h3>` headings (never fake one with bold, never skip a level); every informative
   `<img>` gets DESCRIPTIVE alt (never the filename; decorative &rarr; `alt=""`); real data tables get
   `<th scope>`; every link has text. **You can SEE most Canvas images to write accurate alt** — an
   `<img src>` containing the course's own id and a `?verifier=` token is downloadable
   (`curl -s -o tmp.png -L "<src>"`, then read the PNG and describe what it shows); an image hosted
   in a *different* course (a publisher/master id) may return 403, so use context. Filled navy bands
   look best but raise Ally's **advisory** "use of color" item; drop all `background:` fills for a
   100%-green scan (the "clean" variant in the style guide).
2. **Decide placement deliberately:**
   - **Item type** — a reading/lesson is a **Page**; graded work students submit is
     an **Assignment**; a graded participation thread is a **Discussion**; a
     quiz/exam is a **Quiz**; an in-class / no-submission marker is a **SubHeader**.
   - **Where** — which course, which module (by exact name), and the order within it
     (lesson before its assignment; the exam last).
   - **Points + group** — set `points` and an assignment `group` so the gradebook is
     organized (e.g. Assignments / Midterm / Final Exam).
3. **Manifest + push.** For anything graded or any mixed-type module, use a
   **project manifest** + `Push-CanvasProject.ps1` (see `references/project-course.md`).
   For plain pages only, the lesson manifest + `Push-CanvasPages.ps1` is enough.
4. **Ask publish state**, run `Verify-Slots.ps1` on the pages, push (idempotent),
   then confirm via the API (`GET /modules?include[]=items`, `/assignments`, `/quizzes`).

### Term scheduling & due dates
**Derive, do not ask.** The skill pulls the term **start from the Canvas course
dates** and the **length from the course's own `Week N` modules**, so it normally
needs NO instructor input to schedule. Run `scripts/Set-DueDates.ps1 -Auto`:
- **Start** = the course `start_at` from `GET /courses/:id` (date part); if null it
  falls back to the term start via `GET /courses/:id?include%5B%5D=term` (the bracket
  MUST be percent-encoded as `%5B%5D` or Canvas 404s).
- **Length (weeks)** = the **max N among the manifest's `Week N` modules**. Do NOT use
  Canvas `end_at` for length: `end_at` is the **access-end**, padded past finals
  (e.g. Dec 25 / Dec 17), not the instructional/finals end (full-term finals are
  Dec 7-11). The module count is the reliable length.
- **Finals + breaks** = from the machine-readable term table in
  `references/academic-calendar.md` (a fenced `json` block), looked up by
  `scripts/Get-TermCalendar.ps1`, which infers the term from the start date
  (month >= 8 -> Fall, 1-4 -> Spring, 5-7 -> Summer).

Only **ASK the instructor** when the course is a **blank shell** (no start date AND no
`Week N` modules) or the **term is not in the calendar table**; in those cases `-Auto`
stops with an explicit message. The default due rule is the **chosen weekday (default
Monday) after each week at 11:59 PM**, auto-shifted past holidays, full-break weeks
skipped, with the final on the finals-window end. The **Week-1 anchor** gives Week 1 a
full first week (its due day is the first chosen weekday MORE than 6 days after start),
so a Thursday face-to-face start and the Monday online start produce the SAME table.

**Always SHOW the week-by-week table for approval before applying.** `Set-DueDates.ps1`
is **dry-run by default** (prints the table it would write); pass `-Apply` to write.
For a hand-built table, the explicit path still works: compute with
`scripts/Compute-DueDates.ps1 -AsJson` and pass it via `Set-DueDates.ps1 -DueDatesJson`.
The calendar dates shift yearly, so re-check the source and extend the json table each
year.

The project/capstone section below is the detailed reference for Mode B's graded
pieces (assignments, discussions, quizzes, mixed modules, and the
pages-only-to-gradebook migration).

## Project / capstone courses (assignments + graded discussions)

A lesson course is pages-only. A **project/capstone course** also has a front-page
Home, a Syllabus tab, upload assignments, graded discussions (e.g. weekly
standups), **graded quizzes** (Classic Quizzes, e.g. a final exam), and modules
whose items mix Page / Assignment / Discussion / Quiz / SubHeader. That whole shape
is built by `scripts/Push-CanvasProject.ps1` from a **project manifest** — see
`references/project-course.md` for the manifest schema (including the `quizzes`
question-bank format and assignment `group`s) and the run command. The
page-conversion work (Steps 1-2) is identical; only the manifest and the push
script differ. A graded discussion is a `discussion_topics` POST with an
`assignment[...]` block (Gotcha 5); it also appears in the assignments list, which
is expected. A graded quiz is created unpublished, its questions are (re)built, then
its publish state is set from `-PublishState`; a graded quiz (`quiz_type=assignment`)
likewise carries a backing assignment that shows in the assignments list and
gradebook (also expected).

**Upgrading a pages-only lesson course to a real gradebook:** map each former
"Assignment — X" *wiki page* to an `assignments[]` entry whose `file` is that same
HTML (it becomes the assignment description), parse its points from the page's
`Total: N points` rubric, then **delete the now-redundant wiki page** so students
do not see a duplicate. `references/project-course.md` has the recipe.

---

# Gotchas (these cost real debugging; honor them)

### 1. The Notion fetch can return the WRONG same-prefix page
Notion IDs in one workspace share a long prefix; the fetch tool intermittently
resolves a *different* same-prefix page, non-deterministically — even pulling a
page from another course. **Always run `Verify-Slots.ps1` before pushing.**
When it flags mismatches, the correct page bodies are almost always already on
disk under the wrong filenames (it's a permutation). **Reassemble by content:**
read each correct-content file's `<h2>` to identify it, then write it to the
filename of the slot that wants that content, fixing the eyebrow line and footer
week to match the slot. Read all sources into memory *before* writing any targets
(it's a cycle). Only re-fetch as a last resort, and use the **full dashed UUID**
(`8-4-4-4-12`) which resolves more precisely than the bare 32-char id.
The same flakiness bites **`notion-create-pages`**: the child id it returns can be a
*wrong* same-prefix page. Don't trust that id — confirm the create by re-fetching the
known parent hub and reading its updated child list / Page Index.

### 2. The Canvas tabs API ignores form-encoded bodies
`PUT /courses/:id/tabs/:tab_id` silently no-ops on a form body (returns 200,
changes nothing). Send a **JSON** body with `Content-Type: application/json`.
`Trim-CanvasNav.ps1` already does this — don't "simplify" it back to a form post.

### 3. The Canvas RCE sanitizer is strict
It strips `box-shadow` (so don't rely on it — use borders), and removes `<table>`,
`<script>`, `<style>`, `class=`, `id=` styling, nested `<ul>` in `<li>`, `<ol>`,
and `<br>`. Convert tables to label/value `<div>` rows or a bold heading + a
single-level `<ul>`. Code goes in a `<div>` with `white-space: pre-wrap` (escape
`< > &`). Canvas also auto-appends the school's own theme `<script>` to every page
body — that's expected, not yours.

### 4. Humanize lightly; protect titles and structure
Apply the humanizer to **body prose only**: drop em dashes (use commas/periods/
colons/parens), cut filler/hedging/AI-vocabulary. **Keep em dashes in page titles
and module names** (renaming a module spawns a duplicate, and titles mirror
Notion). Keep emoji tasteful (a goal 🎯, an alert ⚠️, a practice ✅; drop
decorative 💡). Much source prose is the instructor's own writing — the footprint
should be small.

### 5. One H1 = the Canvas page name
Canvas renders the page title as the only `<h1>`, so the hero title is an `<h2>`
and section headings are `<h3>`. Keep it that way for screen readers.

### 6. PowerShell 5.1 reads BOM-less `.ps1` as ANSI
A literal `—` (or any non-ASCII char) in a script string literal becomes `â€"`,
and the smart quote can even break parsing. So: **keep `.ps1` files pure ASCII**;
build an em dash as `[char]0x2014` when you must emit one; read data files with
`-Encoding UTF8`; send request bodies as **UTF-8 bytes**. Best of all, keep
em-dash text (titles, module names) in the **manifest JSON** (read as UTF-8), never
in a script literal — the supplied scripts do this, which is why there is no
title-repair pass.

### 7. Editing a page title regenerates its URL slug
`PUT /pages/:url` with a new title gives the page a new slug, so a stored url goes
stale. Trust the **url in the PUT response** before adding the page to a module
(`Push-CanvasPages.ps1` does this on update). Project pages dodge this by upserting
by a fixed slug and never renaming.

### 8. Module-item creation needs a JSON body
`POST /modules/:id/items` 400s on a form body for Assignment / Discussion /
SubHeader items and is unreliable for Page items. Send **JSON** (`{ "module_item":
{...} }`, UTF-8 bytes). Both push scripts use the `Add-ModuleItem` helper for this.
(Module *create/update/delete* still take form bodies — only the item add is JSON.)

### 9. Notion `replace_content` reorders child PAGES but not inline DATABASES
Inline child databases stay pinned where they are. To position a database, move it
with `move-pages`; don't fight `replace_content`. (Authoring-side, only relevant
when you also restructure the Notion source.)

### 10. Publish state is the user's call — ASK; default unpublished
Pages, assignments, discussions, quizzes, and modules all have a published flag.
Content becomes student-visible the **moment the course itself is published**, and a
**published quiz/assignment with no availability window is immediately takeable** —
so a final exam can go live early by accident. **Before any push, ask published vs
unpublished** and pass `-PublishState`; the fallback is `unpublished`. For exams, also
recommend setting the assignment/quiz `available from / until` and `due_at` dates so
they unlock only during finals.

### 11. The Canvas token is instructor-scoped — treat student data as FERPA
The API token inherits the owner's full permissions: in any course with enrolled
students it *can* read names, emails, login/SIS ids, grades, submissions, and quiz
responses. That capability is exactly why the **Student Data Policy** (top of this
file) limits this skill to content in normal use and routes the only student-data path
through the **sanctioned blind-grading gateway** (`Build-GradingBundle.ps1` ->
`Post-Grades.ps1`), which keeps raw identities local and tokenizes what the model sees.
Outside that flow, **decline** ad-hoc roster / gradebook / submission access. For
grading with real identities use `courseforge-admin`; other real student-data
needs go through the institution's approved process. Never echo or write student PII
(names, emails, ids, grades) into transcripts, logs, or committed files — and never
commit `grading\` (the local `map.json` lives there).

### 12. `PUT /pages/:slug` upserts — reuse the stored slug to avoid duplicates
On this instance `PUT /pages/:slug` creates the page if the slug does not exist and
updates it if it does. So on a re-push, **use the existing slug** (from the state file
that maps `notion_id -> url`) rather than a freshly derived one, or you will create a
second page instead of updating the first. New pages get a clean, stable slug you
control (e.g. `week-15-final-exam`). When you convert an assignment *page* into a real
Assignment object, **delete the old wiki page by its slug** so the two do not coexist.

---

# References (load as needed)
- `references/canvas-api-gotchas.md` — **silent-failure catalog. Read before writing any new script that PUTs to Canvas.**
  Ten ways Canvas returns HTTP 200 and changes nothing (or returns data that does not match what is stored):
  PS 5.1 `ConvertTo-Json` mangling long strings into `{"value":...}`; `@(Invoke-RestMethod ...)` inline nesting the
  array so `.Count` is 1; an **unpublished Classic Quiz serving a stale snapshot** on GET (points/question_count/
  time_limit/description all report pre-publish values while the writes actually landed); `points_possible` not
  recomputing after API question edits; `/assignments/:id/duplicate` 400ing on quiz-backed assignments; the quiz
  question READ shape differing from the WRITE shape; course dates nulling unless
  `restrict_enrollments_to_course_dates` is set; canvas-pii-guard failing closed on URLs built from variables;
  module deletion removing references but orphaning single-referenced content; and assignment shells rejecting
  description writes. Every entry ends in the same rule: **write, then read the field back and compare.**
- `references/style-guide.md` — Canvas-safe component library, palette, accessibility (rich + clean looks).
- `references/ada-remediation.md` — **ADA/Ally remediation playbook** for an EXISTING course: what Ally
  flags + how to clear each, the clean-vs-rich score trade-off (use clean for >=90%), viewing images for
  concise alt (positional fallback for galleries), the scan->restyle->verify->push workflow, write-lock /
  duplicate-slug gotchas, and the PPTX/PDF document reality. Use when asked to "fix my Ally score".
- `references/conversion-spec.md` — the exact per-page conversion prompt (reuse verbatim for workflow agents).
- `references/workflow-pattern.md` — how to fan out the bulk conversion across agents.
- `references/project-course.md` — project/capstone courses: the project manifest (assignments, graded discussions, front page, syllabus, mixed-type module items) and how to push them.
- `references/blind-grading.md` — the OPT-IN blind/pseudonymized grading workflow (sterilizing+pseudonymizing gateway -> grade by pseudonym -> dry-run-first poster), the local-map/never-commit rule, and the screenshot/best-effort caveats.
- `references/academic-calendar.md` — MGCCC term formats + Fall 2026 anchor dates (start/finals/breaks), the source-of-truth calendar URL (re-check yearly), and the DEFAULT due rule used by the due-date scripts.
- `references/slo-alignment.md` — **SLO alignment** for ANY program (automotive, networking, cyber security, cloud, welding, business, health sciences, ...): the workflow, how to write `alignment.json`, what the report gives the instructor, the traps (ambiguous prefixes, local-vs-state course numbers, courses with no outcomes, academic-transfer prefixes with no CTE framework, one PDF under many CIP codes, per-year layout differences, stale deep links), and the PowerShell traps found building it. Read this whenever asked whether a course or program is aligned to its SLOs.

# Scripts
- `scripts/Setup-Canvas.ps1` — **one-command onboarding** (use this first for non-technical users): asks for the course web address + access token (hidden), writes `canvas.token.enc` (DPAPI-encrypted) + `canvas.config.<id>.json`, adds a protective `.gitignore`, and tests the connection. Offers to encrypt a plaintext `canvas.token(.txt)` left in the folder. Params: -WorkingDir, -CourseUrl, -CourseLabel, -ShowToken (no -Token: the token is always typed at the hidden prompt).
- `scripts/Push-CanvasPages.ps1` — idempotent **lesson-course** uploader + module builder (params: ConfigPath, ManifestPath, StatePath, **-PublishState published|unpublished**, **-Apply** — dry run without it).
- `scripts/Push-CanvasProject.ps1` — idempotent **project/capstone** builder: pages + front page + syllabus tab + graded assignments + graded discussions + graded **quizzes** (Classic Quizzes) + assignment groups + mixed-type modules (params: ConfigPath, ManifestPath, StatePath, **-PublishState published|unpublished**, -SkipModules, **-RebuildModules**, **-Apply** — dry run without it). **SAFETY GATE:** its module pass wipes+rebuilds modules, so it REFUSES up front (before any write) on a course whose per-course state file (`canvas.project.<id>.json`, which records the course id) does not show this script built them — pass `-SkipModules` to update content only, or `-RebuildModules` to consciously wipe. For remediating an existing course's bodies use `Push-CanvasRemediation.ps1` instead, never this.
- `scripts/Verify-Slots.ps1` — hero-vs-slot check for **Page** bodies; **run before every push**. (Does not inspect assignments/discussions — spot-check those by hand.)
- `scripts/Trim-CanvasNav.ps1` — nav trim via JSON body; dry run by default, `-Apply` to change; connected course and site only.
- `scripts/Extract-CanvasToken.ps1` — pull a token out of an .rtf in the working folder into an encrypted canvas.token.enc, then offer to delete the .rtf.
- `scripts/Dump-CanvasContent.ps1` + `scripts/restyle_html.py` + `scripts/Push-CanvasRemediation.ps1` — the **existing-course HTML remediation pipeline** (ADA + looks overhaul in one): Dump downloads every body Ally scans (pages/assignments/discussions/quiz descriptions/syllabus; skips quiz-backed shells; strips theme assets) into a workdir + manifest; restyle_html.py `transform --look clean|hybrid|rich` (**DEFAULT `clean`** = NO background fills, so ~0 Ally "use of color" flags — the right choice for a compliance-driven job; navy stays in headings + borders. `hybrid` = filled navy hero+footer, `rich` = all components filled — opt in for a looks overhaul when the instructor accepts ~2-7 reviewable flags/page) deterministically restyles templated bodies and wraps unstructured ones (hero with a real `<h2>` fixes missing headings), entity-encodes to pure ASCII, then `verify` proves visible text unchanged (exit 0 required); Push updates bodies **in place** (never modules/publish state), dry-run default, `-Apply` to write, quiz descriptions via JSON, live re-verify after push. Full workflow + gotchas: `references/ada-remediation.md`.
- `scripts/Export-CanvasCourse.ps1` — export a whole course to a local `.imscc` (IMS Common Cartridge) backup: starts the export, polls to completion (handles Canvas's `waiting_for_external_tool` state), downloads the file (params: -CourseId/-ConfigPath/-TokenPath, **-ExportCourseId** to export a different course the token can read, -OutDir, -ExportType common_cartridge|zip). **FERPA edge:** exports of TAUGHT courses can bundle student-authored discussion entries inside the cartridge — prefer master/unpublished shells for other instructors' courses and keep every .imscc local (never commit).
- `scripts/Import-CanvasCourse.ps1` — import INTO a course, two modes: **-SourceCourseId** = Canvas-to-Canvas copy (`course_copy_importer` — the replica/sandbox-clone flow) or **-ImsccPath** = upload + import a local cartridge. Optional **-NewCourseName + -AccountId** creates a fresh unpublished shell first (needs course-creation rights; a content-only admin role gets a clear 403 message and should use a UI-created shell + -DestCourseId). **Dry-run by default; -Apply to run.** SAFETY: refuses a destination that already has pages/modules unless **-Force** (import ADDS content — protects against accidental duplication); never touches publish state; polls the migration to completed/failed and reports true post-import counts. Duplicate-title pages from a copy get Canvas's `-2` slug suffix (expected).
- `scripts/CanvasContext.ps1` — shared config/token resolver used by the push/dump/pptx scripts: explicit `-ConfigPath` wins; else `canvas.config.<CourseId>.json` when `-CourseId` given; else the SINGLE `canvas.config.*.json` in the current dir, then `Documents\canvas-work`; token = `canvas.token` next to the chosen config; multiple matches = hard error (never a silent pick). **Designers working many courses: keep one folder per course** (config + token side by side) and run scripts from that folder or pass `-CourseId`.
- `scripts/Batch-Remediate.ps1` — **run the whole remediation pipeline across MANY courses at once** (the designer's real job): per-course subfolder + generated config, Dump → transform (`-Look hybrid|rich|clean`) → verify → Push per course, aggregate roll-up table + `batch-summary.md`. Dry-run default (`-Apply` to write), `-StopOnError` optional (continue is the default), verify failure skips that course's push. One token serves the whole batch.
- `scripts/Remediate-CanvasDocx.ps1` + `scripts/remediate_docx.py` — **automated Word (.docx) ADA remediation**, same two-phase gateway as PPTX: `List` / `Fetch` (downloads + scans: images with missing/filename alt, documents with no real Heading styles + faux-bold heading candidates with paragraph indexes, tables without a repeating header row) → agent VIEWS extracted images and writes `fixes.json` (alts; **opt-in** paragraph→Heading promotions — they change appearance; `table_headers`) → `Push` (apply → re-verify → overwrite-upload, original kept). HONEST SCOPE: alt + headings + table header rows, not full document tagging.
- `scripts/Fastlane-CanvasPdfs.ps1` + `scripts/pdf_fastlane.py` — **FAST deterministic PDF ADA pipeline** (the DEFAULT for "fix my course's PDFs"): List/Fetch/Process/Push gateway; the parallel python engine fixes the mechanical majority in seconds with NO model round-trips — OCR text layers (tesseract word-box overlay, pixels untouched), real basic tag trees (P/H1 + Figure MCIDs, all other content artifact-marked per PDF/UA 7.1), title/lang/DisplayDocTitle; already-tagged files (Word exports) get a metadata-only light touch — the dominant "missing title" fix; CAD /OC layer marked-content handled. Refusals (encrypted / signed / foreign MCIDs) and any verify failure land in `queue.json` for the MODEL: fix the FILE, or when one reason repeats patch the ENGINE and re-run `python pdf_fastlane.py selftest` (add a fixture first) before re-batching — Process is incremental. Alt text is the one model-speed step, batched + hash-deduped (`figures` -> view PNGs -> write alt.json -> `apply-alt`); figures are located by MCID so vector art (Word shapes/charts) is pictured too, authored alt is never overwritten, and figures that CANNOT be pictured or that carry filename-only alt are counted and named in `alt-summary.json` rather than passing silently. The scoreboard is **PDF/UA-1 (ISO 14289-1) via veraPDF** (`validate`; custom `--profile` XML like WTPDF 1.0 accepted), NOT course-scanner heuristics. Every output independently verified (text preserved, render pixel-identical, tree present) or it is deleted and queued. Measured: 43 blueprint PDFs ~17s; 220 math PDFs ~38s. Push = dry-run default, overwrite-upload keeps links, originals kept locally. Workflow + gotchas: `references/pdf-fastlane.md`.
- `scripts/Triage-CanvasPdfs.ps1` + `scripts/triage_pdf.py` — **PDF accessibility TRIAGE, detection only** (use Fastlane above to actually fix): classifies every course PDF as `scanned-image` (no text layer — needs OCR/re-sourcing; the worst Ally offenders), `text-untagged` (readable but no headings/reading order), `tagged` (spot-check), or `encrypted`; ranked console table + `triage-report.md`. Real full-semantic PDF/UA tagging stays a manual Acrobat job.
- `scripts/Remediate-CanvasPdfText.ps1` + `scripts/pdf_text_tool.py` — **PDF text SCAN, targeted in-place EDIT, and form FILL** (needs `pip install PyMuPDF`): same gateway as PPTX/DOCX — `-Action List`, `-Action Fetch -Pattern <regex>` (downloads + scans text AND metadata, **flags hazards**: digital signatures, low/no-text pages, outline/TOC), agent writes `map.json` (`[{find, replace, limit?}]`), optional `-Action Fill` (writes values onto flattened/blank forms from `fill.json` — anchored to a label or absolute coords), `-Action Push` (apply → verify zero residual incl. TOC text → overwrite-upload; dry-run default). SAFETY: **refuses signed PDFs** (`-StripSignature` = honest unsigned output, `-AllowSigned` = will read as ALTERED); **nested/overlapping matches auto-skipped** (never double-drawn — list the most specific find first); `-UpdateToc` rewrites outline entries; `fill` **plans all placements first and REFUSES on collisions** with existing text / other placements / the page edge (underscore-rule runs count as the blank, not a collision; `allow_overlap` per item to override), warns when a value sits off its anchor's baseline (wrong-blank hazard), and supports `--preview` (numbered green/red boxes PNG) + `--dry-run`. HONEST SCOPE: short factual strings drawn in base-font Helvetica (visible next to embedded fonts; shorter replacement leaves a gap); wrapped phrases = one mapping per rendered line; extraction quirks (checkbox glyphs read as `D`/`0`/`o`) mean **scan first, copy exact strings**; does NOT create tagged/accessible PDFs and cannot re-sign. **After any write, RENDER changed pages to PNG and LOOK** — text checks prove strings, only eyes prove layout (a text-presence check once passed on values overprinting a column header).
- `scripts/Remediate-OfficeText.ps1` + `scripts/office_text_tool.py` — **Office (.docx/.pptx/.xlsx) text SCAN + EDIT at the raw-XML level** (stdlib only): the change-request tool for documents — replace a former instructor's name/email/room, fix stale dates, set Author/LastModifiedBy (`-SetAuthor`/`-SetLastModifiedBy`) — reaching `docProps/core.xml`, slide masters/layouts, headers/footers, and notes that the alt-text remediators never touch. Same List/Fetch/Push gateway: scan writes per-file `scan.json`, agent writes `map.json`, Push applies (replacements auto XML-escaped; `&`-containing finds also matched in escaped form), **verifies zero residual + zip integrity (refuses upload otherwise)**, overwrite-uploads. Legacy binary `.doc/.ppt/.xls` are **detected by OLE magic and refused with a convert-first message** (List tags them `[LEGACY]`), never silently skipped. CAVEAT: matching is literal against raw XML — Word can split a phrase across runs mid-word; a mapping that never matches is reported, and `scan` shows the exact XML context to copy from.
- `scripts/Check-SLOAlignment.ps1` + `scripts/slo_framework_tool.py` — **SLO alignment: cross-reference a Canvas shell against the Mississippi Student Learning Outcomes for its program**, for ANY program. Answers "which of my assignments align with my program's SLOs, and where are the gaps?" Same gateway shape as the PPTX/DOCX/PDF remediators: `-Action Resolve` maps the Canvas `course_code` prefix to candidate MCCB programs via the framework index (refusing to guess when a prefix serves several programs, e.g. `IST` -> 10); `-Action Fetch` downloads the program's current (or `-UsePrior`) framework PDF, extracts THAT course's nested Student Learning Outcomes, and inventories every Canvas item that could carry evidence (assignments, quizzes, discussions + ungraded pages) into `slos.json` / `items.json`; the **AGENT** then writes `alignment.json` mapping each lettered sub-outcome to the items that assess it with a verdict, rationale and a suggested fix per gap; `-Action Report` **validates** that mapping and renders markdown + Canvas-safe HTML (dry-run default, `-Apply` pushes it as an UNPUBLISHED instructor-facing page). The validator **fails (exit 2)** on an omitted outcome, an invented outcome id, an item id not in the course, or coverage claimed with no evidence, so an audit-facing report cannot hand-wave. Judges SLO verbs literally: an outcome that says create/implement/compile is NOT satisfied by an essay. Handles academic-transfer prefixes (ENG, MAT, CSC) by reporting that no CTE framework governs them rather than forcing a match. Content-plane only; never touches submissions, grades or roster data. Details + traps: `references/slo-alignment.md`.
- `scripts/Backup-CanvasQuiz.ps1` — **snapshot a Classic Quiz before you rewrite it.** Creates an ALWAYS-unpublished in-course copy (same settings) and re-POSTs every question, because `POST /assignments/:id/duplicate` returns 400 for quiz-backed assignments. Maps the answer READ shape to the WRITE shape (`text`/`left`/`right`/`weight` -> `answer_text`/`answer_match_left`/`answer_match_right`/`answer_weight`), so matching and multi-answer questions survive intact. Verifies via `/questions` rather than the quiz object (an unpublished quiz reports stale totals). Prints `BACKUP_QUIZ_ID=<id>`; dry-run default, `-Apply` to write. Params: -QuizId, -ConfigPath, -TokenPath, -CourseId, -TitlePrefix, -Apply.
- `scripts/Fix-BoldAsStructure.ps1` — clears Ally's **"Styles might be used instead of semantic markup for structure"** across pages, assignments and discussions. The check fires on any paragraph whose ENTIRE content is one emphasis element (`<p><strong>...</strong></p>`), regardless of length — bolding a word or phrase *inside* a sentence is fine. **Default is REPORT ONLY**, classifying each hit as `label` / `sentence` / `code` / `unclassified`, because the message's "use proper headings" advice is wrong most of the time: one course had ~70 hits and only ~16 wanted a heading. Three opt-in remedies: `-PromoteLabels` (allowlisted labels -> real `<h3>`; **refuses if the body has no `<h2>`**, which would just trade this flag for "skipped headings"), `-UnboldSentences` (drop the blanket bold on instruction sentences — a 94-char sentence must not become a heading), `-ConvertCodeRuns` (collapse code faked with bold + `&nbsp;` indentation into ONE code block, `&nbsp;` back to real spaces, no background fill so the clean look stays Ally-green). Gated on visible text being byte-identical. Params: -ConfigPath, -TokenPath, -CourseId, -TitleFilter, -Labels, -SentenceMinLength, the three remedy switches, -Apply. Triage rules: `references/ada-remediation.md`.
- `scripts/Remove-BorderedBoxes.ps1` — **course-wide cleanup of inline `<span style="border: 1px solid #d7dce3">` emphasis boxes.** These fragment when text wraps (a sentence in a ragged open box) and convey emphasis by decoration alone, which a screen reader announces as nothing. Sweeps pages, discussions/announcements, quiz descriptions, quiz question text, assignment descriptions and the syllabus. Surgical: unwraps the span only when the border is the whole style, otherwise deletes just the border declaration and keeps co-declared styling (e.g. `font-size: 14pt`). Never touches `<div>` cards/heroes (which legitimately use the same grey border) or gold pills. Gated on visible text being unchanged. Params: -ConfigPath, -TokenPath, -CourseId, -BorderColor, -Apply.
- `scripts/Push-CanvasRubrics.ps1` — create **REAL Canvas Rubric objects** and attach them to assignments (drives SpeedGrader/gradebook; optionally `use_for_grading`), from a JSON manifest of criteria+ratings. Idempotent by title (updates, never duplicates), dry-run default, points-sanity warnings. Rubric DEFINITIONS only — rubric *assessments* are per-student scores and stay guard-denied.
- `scripts/Remediate-CanvasPptx.ps1` + `scripts/remediate_pptx.py` — **automated PPTX ADA remediation** (needs `pip install python-pptx`): `-Action List` enumerates course decks, `-Action Fetch` downloads (keeps `original.pptx` backup) + scans (extracts images + `report.json`), then the AGENT views each image and writes `work\fixes.json` (concise alt / `""` decorative / slide titles), then `-Action Push` applies + re-verifies + uploads over the original so links keep working (dry-run default, `-Apply` to upload). Contrast issues are report-only; never touches submission attachments. See the PPTX section of `references/ada-remediation.md`.
- `scripts/Build-GradingBundle.ps1` — OPT-IN blind-grading **sterilizing + pseudonymizing gateway**: fetches submission text, writes a LOCAL `grading\<id>\map.json` (gitignored, never read by the model) and a scrubbed, pseudonymized `bundle.json` to grade from. Scrubs structured PII FIRST (email/phone/SSN/MGCCC ids/**bare 8-10 digit runs**) then names (**all roster full-name forms** for peer mentions + the author's tokens and login_id); **verifies the finished bundle and exits 2 on residual hits** unless `-Force`. `-IncludeAttachmentText` extracts + scrubs `.docx`/`.pdf`/`.txt` attachment text via `extract_attachment_text.py` (images stay filename-only on purpose); `-KeepLongNumbers` relaxes bare-digit redaction for numeric assignments (params: -ConfigPath, -AssignmentId, -TokenPath, -OutDir, -IncludeAttachmentText, -KeepLongNumbers, -Force). Sanctioned by `canvas-pii-guard` — and the sanction covers the whole command line, so run it in its OWN command and never chain a `map.json` read onto it.
- `scripts/Post-Grades.ps1` — pseudonym-aware grade poster: reads `map.json` + `proposed-grades.json`, resolves each pseudonym to a user_id, **dry-run by default**, `-Apply` to post; refuses unknown pseudonyms; live-course warning + audit (params: -ConfigPath, -AssignmentId, -TokenPath, -OutDir, -Apply). Sanctioned by `canvas-pii-guard`.
- `scripts/Compute-DueDates.ps1` — compute a week-by-week due-date table from a term start, week count, finals-window end, and break ranges (default due = the chosen weekday after each week at 23:59, auto-shifted past holidays, full-break weeks skipped, final on the finals end). Week-1 anchor = first chosen weekday >6 days after start (start day-of-week no longer shifts the schedule). Deterministic (`ParseExact`); prints a table and supports `-AsJson` (params: -StartDate, -Weeks, -FinalsEnd, -Breaks, -DueTime, -DueWeekday, -AsJson).
- `scripts/Get-TermCalendar.ps1` — read the machine-readable term table (fenced `json` block) from `references/academic-calendar.md`; infer the term from a start date (month >= 8 -> Fall, 1-4 -> Spring, 5-7 -> Summer) and return its `finalsEnd` + `breaks`, or nothing if the term is not in the table (caller then asks the instructor). Dot-source it for the `Get-TermCalendar` / `Get-TermName` functions, or run standalone (params: -StartDate, -CalendarPath).
- `scripts/Set-DueDates.ps1` — apply a due-date table to a course's assignments + quizzes by reading the project manifest: maps each "Week N" module to its DueAt, resolves each Assignment/Quiz item by name/title to its Canvas id, and PUTs `assignment[due_at]`/`quiz[due_at]`. Skips "Start Here"; **dry-run by default**, `-Apply` to write. Two ways to supply the table: explicit `-DueDatesJson` (a `Compute-DueDates -AsJson` file), or **`-Auto`** which derives it with no instructor input — start from the Canvas course `start_at` (term-start fallback via `?include%5B%5D=term`), length from the manifest's max `Week N` (NOT raw `end_at`, which is the padded access-end), finals+breaks from `Get-TermCalendar`; stops and asks only for a blank shell or an unknown term (params: -ConfigPath, -ManifestPath, -DueDatesJson, -Auto, -DueTime, -DueWeekday, -TokenPath, -Apply, -WhatIf).
