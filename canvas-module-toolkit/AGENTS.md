# Canvas Module Toolkit — agent playbook

Update Canvas LMS course modules (pages, assignments, quizzes): restyle to an
institution's brand, refresh content for currency, and fix factual/scoring errors —
safely, cheaply, and the same way regardless of which AI coding agent is running this.

**Read this whole file before doing anything.** It is short on purpose: everything
deterministic lives in `scripts/`, so you read PASS/FAIL output instead of re-deriving
rules by reasoning. That split is the whole reason this is cheap to run.

## What this is / is not

- **Is:** a content-editing toolkit for Canvas *course content* — page bodies,
  assignment descriptions, quiz descriptions and questions.
- **Is not:** a grading, roster, or submissions tool. Never call Canvas endpoints under
  `/enrollments`, `/users`, `/students`, `/submissions`, `/grades`, or similar. If asked
  to do something involving student data, decline and say why.

## Prerequisites (check once per machine, not per task)

- **PowerShell 7+** (`pwsh`) — Windows PowerShell 5.1 also works on Windows.
  macOS/Linux: `brew install --cask powershell` if `pwsh` is missing.
- **Python 3** (`python3`) — used only for the validator scripts, no packages beyond
  the standard library.
- **A Canvas config + token**, next to each other in one folder:
  - `canvas.config.<courseId>.json` — `{ "base_url": "https://school.instructure.com", "course_id": "12345" }`
  - `canvas.token` — one line, the API token. **Never print, log, or commit this file.**
  - `scripts/lib_canvas.ps1`'s `Resolve-CanvasConfig` looks in the current directory,
    then `~/Documents/canvas-work`. Pass `-ConfigPath` explicitly if neither applies.
  - If no token exists yet, tell the user to generate one at **Canvas → Account →
    Settings → New Access Token**, and where to save the two files. Never ask them to
    paste the token into chat; have them save it to a file instead.

## The procedure, one module at a time

Work **one module at a time**. Do not dump an entire course into context — scope every
step to the module you're updating.

### 1. Explore (read-only)

```bash
pwsh -NoProfile -File scripts/Get-CanvasModule.ps1 -ModuleId <id> -WorkDir ./work/m<N>
```

This dumps every item, every instructor-authored HTML body, every quiz question with
its current answer key, and an `ids.json` you'll reuse in the changeset later. Read the
dumped bodies directly from disk — don't re-fetch them by hand.

### 2. Judge, don't assume (this is the part that needs a model, not a script)

For each restylable body:
- **Read the quiz answer keys.** For every question, confirm exactly one option is
  actually correct given the source material. This has caught real, live scoring bugs
  before (a correct-answer key pointing at the wrong law, a stale document reference) —
  don't skip it because the quiz "looks fine" at a glance.
- **Check named facts that can go stale**: standard/document version numbers (e.g. "ISO
  27001:2013" vs. a later edition), named laws or frameworks, statistics. Cross-check
  against other content in the same module first — a mismatch between two of your own
  files is often the cheapest way to catch an error (e.g. the quiz cites the right
  document, the assignment cites a different, wrong one).
- **Search the web only for a specific, named, checkable claim** — a standard's current
  edition, a real statistic, whether a named act/framework is still in force. Don't do
  a blanket "verify everything" pass; that's slow and mostly finds nothing. If a search
  comes back ambiguous or contradictory, say so and leave the original text alone
  rather than asserting a fix you're not confident in.
- **View images before writing alt text.** Fetch the image (its Canvas `preview` URL is
  usually directly downloadable with the same bearer token) and describe what it
  actually shows. Never leave a filename, or a vague two-word guess, as alt text.

### 3. Author

Write the new HTML/quiz content:
- Follow your institution's style guide (`references/style-guide.example.md` is one
  worked example — copy and adapt it, or use your own; either way, keep a written style
  reference so you're not re-deriving formatting rules from memory each time).
- Preserve instructional text **verbatim** unless you are deliberately fixing an error
  or adding new material the user asked for. Restyling markup is not license to rewrite
  the instructor's voice.
- Quiz question rebuilds must keep the **same question count** unless the user
  explicitly asked to change it — Canvas quiz points are 1-per-question by default, so
  a silent count change silently changes the quiz's total points.

### 4. Validate — before anything touches Canvas

```bash
python3 scripts/check_style.py new/page.html --palette references/palette.example.txt
python3 scripts/check_quiz.py new/quiz.json --expect-count 14
python3 scripts/diff_content.py work/m7/bodies/01_original.html new/page.html
python3 scripts/check_contrast.py "#2c3a4d" "#ffffff"   # only if you changed colors
```

Read the PASS/FAIL/RESULT lines. Don't re-verify by eyeballing the HTML — that's the
expensive way to do exactly what these scripts already did deterministically. If a
check fails, fix the file and re-run; do not proceed with a failing style or quiz check.

### 5. Push — dry-run first, always

Write a changeset (`examples/changeset.example.json` is the template;
`references/changeset-schema.md` documents the full format):

```bash
pwsh -NoProfile -File scripts/Push-CanvasModule.ps1 -Changeset ./changeset.json
```

This defaults to a **dry run** — it prints exactly what would change (body sizes,
points, due dates staying untouched, question counts, the pre-flight answer-key audit,
and the course's publish state) without writing anything. Read that plan. Only then:

```bash
pwsh -NoProfile -File scripts/Push-CanvasModule.ps1 -Changeset ./changeset.json -Apply
```

Rebuilding quiz questions refuses to run unless the **course itself** is unpublished
(the only state where student attempts provably cannot exist yet) — this is enforced
by the script, not left to your judgment, because it's the one mistake in this whole
workflow that can destroy real student data. Do not pass
`require_unpublished_course: false` unless you've independently verified no attempts
exist through a sanctioned, read-only path.

### 6. Verify live, then report

Re-fetch the page/assignment/quiz you just changed (a plain `GET`, or re-run
`Get-CanvasModule.ps1`) and confirm: the new content is there, structure counts match
what you sent, and — for quizzes — every question still has exactly one correct answer
key. Then tell the user, concisely: what changed, any bugs you found and fixed (name
them specifically), what you deliberately left untouched and why, and confirm nothing
outside the intended scope (module order, points, due dates, publish states, other
items) moved.

## Non-negotiable rules

1. Never touch rosters, grades, or submissions endpoints.
2. Dry-run before every write. Read the plan before approving your own `-Apply`.
3. Never rebuild quiz questions on a course that might have real student attempts.
4. Never change points, due dates, module order, or publish state unless asked.
5. Preserve instructional content verbatim except where you're fixing a named error or
   adding requested material — say which is which in your report.
6. Ask before publishing anything new, or before writing to a non-unpublished course.

## Why this setup is cheap to run

- Compliance and quiz-key checks are **scripts**, not reasoning — a `RESULT: N passed,
  0 failed` line costs a fraction of what re-deriving the same judgment in prose costs,
  every single time you run it.
- `Push-CanvasModule.ps1` is **one generic, parameterized script** for every module —
  there is no per-module script to write, copy, or edit by hand.
- Work is scoped to one module's own dump, not the whole course.
- Web search is reserved for specific, named, checkable claims — not a blanket
  verification pass.
