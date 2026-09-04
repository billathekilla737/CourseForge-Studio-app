# Project / capstone courses (assignments + graded discussions)

The lesson-course pipeline (pages -> modules -> nav) covers most courses. A
**project or capstone course** adds four things a lesson course does not have:

- a **front-page Home** that is the course landing view,
- a **Syllabus tab** (Canvas's built-in `syllabus_body`, not a wiki page),
- **upload assignments** (deliverables students submit), and
- **graded discussions** (e.g. weekly standups / proof-of-work),

and its **modules mix item types** — a week module might hold a Page, two
Assignments, a Discussion, and a SubHeader note ("Peer review in class").

`scripts/Push-CanvasProject.ps1` builds all of this from one project manifest. It
is the generalized form of the original one-off capstone scripts (which were
hardcoded to a single course id and needed a separate title-fix pass — see the
encoding note below for why that pass is gone).

## When to use which script
- **Lesson course** (pages only) -> `Push-CanvasPages.ps1` + `manifest.<id>.json`.
- **Project/capstone course** (pages + assignments + discussions + front page +
  syllabus) -> `Push-CanvasProject.ps1` + a **project manifest** (below).

## Project manifest shape
```json
{
  "course_label": "IMT 2772 — Simulation and Game Project",
  "syllabus_file": "canvas-export/pages/IMT2772/syllabus.html",
  "pages": [
    { "slug": "home",   "title": "Home", "file": "canvas-export/pages/IMT2772/home.html", "front_page": true },
    { "slug": "week-1", "title": "Week 1 — Kickoff & Pitches", "file": ".../week1.html" },
    { "slug": "pitch-one-pager", "title": "Pitch One-Pager (Template)", "file": ".../tpl-pitch.html" }
  ],
  "assignments": [
    { "key": "gdd", "name": "Game Design Document", "file": ".../assign-gdd.html",
      "points": 25, "submission_types": ["online_upload"], "due_at": "2026-06-03T23:59:00" },
    { "key": "final", "name": "Final Submission", "file": ".../assign-final.html",
      "points": 100, "submission_types": ["online_upload"], "due_at": "2026-07-28T12:00:00" }
  ],
  "discussions": [
    { "key": "standup-1", "title": "Week 1 Standup — Kickoff", "file": ".../discussion-weekly-standup.html",
      "points": 10, "due_at": "2026-05-27T23:59:00",
      "note": "locked team (2-5), path, one-line pitch, Trello + Unity repo set up.",
      "note_label": "This week's milestone:" }
  ],
  "quizzes": [
    { "key": "final", "title": "Final Exam — Game Theory", "file": ".../week15-final-exam.html",
      "group": "Final Exam", "time_limit": 60, "shuffle_answers": true,
      "questions": [
        { "text": "The MDA framework stands for Mechanics, Dynamics, and ___.", "type": "short_answer_question",
          "points": 3, "answers": [ { "text": "Aesthetics", "correct": true } ] },
        { "text": "A core loop is the smallest repeatable cycle of player actions.", "type": "true_false_question",
          "points": 2, "answers": [ { "text": "True", "correct": true }, { "text": "False", "correct": false } ] },
        { "text": "Which is a reward schedule?", "type": "multiple_choice_question", "points": 3,
          "answers": [ { "text": "Variable ratio", "correct": true }, { "text": "Greybox", "correct": false },
                       { "text": "Magic circle", "correct": false }, { "text": "Juice", "correct": false } ] }
      ] }
  ],
  "modules": [
    { "name": "Start Here", "items": [
      { "type": "Page", "slug": "home" },
      { "type": "SubHeader", "title": "Read the Syllabus tab before Week 1." } ] },
    { "name": "Week 1 — Kickoff & Pitches", "items": [
      { "type": "Page", "slug": "week-1" },
      { "type": "Assignment", "key": "trello" },
      { "type": "Discussion", "key": "standup-1" },
      { "type": "SubHeader", "title": "Peer Review — Round 1 (in class; instructor shares the form link)" } ] },
    { "name": "Week 15 — Final Exam", "items": [
      { "type": "Page", "slug": "week-15-final-exam" },
      { "type": "Quiz", "key": "final" } ] }
  ]
}
```

Notes:
- **`key`** is your local handle. The script resolves `key` -> the real Canvas id
  it gets back on create, then references it from `modules[].items`. You never put
  a Canvas id in the manifest. (Works for assignments, discussions, and quizzes.)
- **`slug`** is the page URL. Pages upsert by slug (`PUT /pages/:slug`), so the
  slug is stable across re-runs — that is what keeps module item links valid.
- **`note`** on a discussion prepends a gold callout box to the shared body (used
  for the per-week milestone line). Omit it for a plain discussion.
- A discussion with an `assignment[...]` block also shows up in the assignments
  list — that is expected, not a bug (Gotcha 5).
- **`quizzes`** build Canvas **Classic Quizzes** (`quiz_type=assignment`, so they
  land in the gradebook). The quiz is created unpublished, its questions are
  (re)built — old questions are deleted first so re-runs do not duplicate — then it
  is published. `file` becomes the quiz description; question `points` should sum to
  the exam total. Allowed `type`: `multiple_choice_question`, `true_false_question`,
  `short_answer_question` (fill-in-blank; list 1-3 acceptable `answers`),
  `essay_question` (no `answers`, manual grade). Place it with a `Quiz` module item.
- **`group`** (on any assignment or quiz) names a Canvas **assignment group** for
  gradebook organization; the script reuses an existing group or creates it. Omit to
  use the course default.
- **Migrating a pages-only course to graded items:** map each former "Assignment —
  X" wiki page to an `assignments[]` entry whose `file` is that same HTML (the
  description), parse its points from the page's `Total: N points` rubric, then
  **delete the now-redundant wiki page** so students do not see a duplicate.

## Run it
```powershell
scripts\Push-CanvasProject.ps1 `
  -ConfigPath   .\canvas.config.<id>.json `
  -ManifestPath .\canvas-export\project.<id>.json `
  -StatePath    .\canvas.project.<id>.json `
  -PublishState unpublished   # ASK the user first; default unpublished (SKILL Gotcha 10)
# -WhatIf to plan, -SkipModules for a content-only pass
```
`-PublishState published|unpublished` sets the published flag on every page,
assignment, discussion, quiz, and module this run touches (the quiz is always created
unpublished so questions can be added, then set to the chosen state). **Always ask the
user before pushing.** For a graded **quiz/exam**, also set an availability window
(`available from`/`until`) and `due_at` so it cannot be taken early once the course is
published.

Idempotent: pages upsert by slug, assignments by name, discussions by title.
**Modules are rebuilt from scratch each run** (cleared, then recreated in manifest
order) so item ordering is always correct — safe because module structure carries
no per-student data.

## Why there is no separate "fix titles" pass anymore
The original capstone wrote em-dash titles as PowerShell string *literals*, which
PS 5.1 read as ANSI and mangled (`—` -> `â€"`), forcing a remediation script
(`Fix-Capstone-Titles.ps1`). `Push-CanvasProject.ps1` keeps every title in the
**manifest JSON** (read as UTF-8) and the script body pure ASCII, so the corruption
never happens. See SKILL.md Gotcha 6.

## Verification
`Verify-Slots.ps1` checks Page bodies against their slots (hero `<h2>` vs title);
it does **not** inspect assignments or discussions. After a project push, spot-check
those by hand: `GET /assignments?per_page=100` and `GET /discussion_topics?per_page=100`
should list the expected names/titles with the right points and due dates, and each
graded discussion should carry an `assignment` object.
