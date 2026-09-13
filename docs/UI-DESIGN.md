# CourseForge Studio: web UI and navigation

The design contract for the Studio's front end. Every area follows it, so the
five areas read as one tool. The grader's shell (`courseforge/web/`) is the
base; grading code is moved, not rewritten.

Stack: vanilla JS, no build step, no framework. Background jobs are polled at
700 ms via `GET /api/jobs/<id>`. Every Canvas write is refused once by the
server with a confirm token and shown as a plain sentence (`askConfirm`).

## 1. Routes

| Route | Opens | File |
|---|---|---|
| `#/` | Courses picker plus an "Across your courses" strip (Term schedule, Accommodations, Batch Course Restyle) | grade.js / hub.js |
| `#/schedule` | Term schedule (existing) | grade.js |
| `#/batch` | Batch Course Restyle across courses | a11y.js |
| `#/c/<cid>` | **Course hub** (new) | hub.js |
| `#/c/<cid>/grade` | Assignment list (existing `openCourse`) | grade.js |
| `#/c/<cid>/a/<aid>` | Grading workspace (existing, untouched) | grade.js |
| `#/c/<cid>/a11y` and `/a11y/<kind>` | Accessibility: `html` `pptx` `docx` `pdf-text` `office-text` gateways | a11y.js |
| `#/c/<cid>/a11y/pdf`, `/pdf/alt`, `/pdf/prove` | PDF fixer, alt-text grid, compliance census | a11y.js |
| `#/c/<cid>/build`, `/build/new/<kind>`, `/build/manifest`, `/build/rubrics` | Build content | build.js |
| `#/c/<cid>/tools/<sub>` | `dates` `export` `import` `clone` `nav` `quiz-backup` `slo` | tools.js |
| `#/c/<cid>/assistant` | Assistant panel | assistant.js |
| `#/c/<cid>/record` | Record of actions: the chain, where it is kept, filters by student | record.js |

Router (core.js), in this order:

```js
if (parts[0] === 'schedule') return openSchedule();
if (parts[0] === 'batch') return openBatch();
if (parts[0] === 'c' && parts[2] === 'a') return openAssignment(parts[1], parts[3], { arriving: true });
if (parts[0] === 'c' && parts[2] === 'grade') return openCourse(parts[1]);
if (parts[0] === 'c' && AREAS.has(parts[2])) return AREAS.get(parts[2]).open(parts[1], parts.slice(3));
if (parts[0] === 'c') return openHub(parts[1]);
return openCourses();
```

Grading edits required by the route change: the crumbs in `openCourse` become
`Courses / <course> (#/c/<cid>) / Grade`, and the course crumb in
`openAssignment` links to `#/c/<cid>/grade`. Nothing else in grading moves.

## 2. Navigation

1. Picker: course cards, plus the cross-course strip.
2. Course hub: five area cards with live status from local state only
   (`GET /api/courses/<cid>/hub`), a `stale` flag triggering a background refresh.
3. Area bar (`<nav id="areaBar">`, second row under the header): Grade,
   Accessibility, Build, Tools, Assistant, Record. `aria-current="page"` on the
   active tab; a small count badge for queued PDFs or a pending Allow/Deny.
   Record carries its own `--record` zone rather than borrowing Tools', or the
   bar lights the wrong tab.
4. The area bar is hidden inside the grading workspace, on the picker and on
   the schedule (the work pane is position fixed under the header).

Hub card lines, for example: Accessibility "HTML: 41 items, verified 3 days
ago (clean look), pushed. PowerPoint/Word: 8 files, 4 need alt text. PDFs: 218
files, 197 pass PDF/UA-1, 6 queued for a person." Empty state: one sentence
saying what the first action does and that nothing is pushed.

Below the cards: "Install X to enable" cards when a tool is missing, and "What
changed in this course", the last eight Canvas writes across all areas
(`GET /api/courses/<cid>/ledger`).

## 3. Shared area shell

```
section#viewArea.hidden
  .areaHead        h1#areaTitle · span#areaHint · div#areaActions
  div#areaTabs[role=tablist]
  div#areaBody[role=tabpanel]
```

`showView('area')` sets `document.body.dataset.area`, which sets `--zone` and
`--zone-soft`. An area's `open()` must call `showView`, `crumbs`, fill
`#headerActions`, then render tabs and body.

## 4. The gateway (specified once, used five times)

`renderGateway(host, spec)` for `html`, `pptx`, `docx`, `pdf-text`,
`office-text`, and `slo`. Five steps, one forward button each:

| Step | Pane | Button | Canvas? |
|---|---|---|---|
| List | table: checkbox, name, type, size, modified, state pill; chips All / Not fetched / Has issues / Verified / Pushed | Fetch & scan N | read |
| Scan | same table plus an Issues column and a summary sentence | Review fixes; optional "Describe images with Claude" (`.btn.ai`) | no |
| Review | `spec.reviewRenderer`; every item shows verify pills (text identical, links unchanged, images unchanged) or the failing check with "excluded from push" | Dry run | no |
| Dry run | `.applyList .planned` rows; items that failed verify under "Will not be pushed" | Apply N (`.btn.danger`, `runJobConfirmed`) | asks first |
| Apply | rows with the live re-verify result; "Restore previous bodies" also confirms | | writes |

Review renderers: html = before/after panes (`.paperFrame .canvasHtml.asIs`);
pptx/docx = AltGrid plus a table of non-alt fixes (opt-in for anything that
changes appearance); pdf-text/office-text = find/replace map table with
hazards as callouts; slo = resolve pane then outcome table with verdict pills.

## 5. PDF fixer

Verb row: Refresh file list, Back up & fix (primary), Describe images (`.btn.ai`),
Upload (`.btn.danger`), Prove compliance, Back up only, Roll back (`.btn.danger`).
Stat strip: Files, Fixed & verified, Pass PDF/UA-1, Queued for a person, Not yet uploaded.
File table columns: Name (+folder), Pages, Lane pill (`full` `light` `refused`
`queued` `review`), State, Verify (text / render / tree ticks), Compliance
(pass, N rules, not checked, needs veraPDF), Alt (described / placeholders / no picture).
Queue panel: grouped by reason, each item with severity, hint, Open folder, Mark handled.

Alt-text grid (shared AltGrid): card per figure with the picture, kind pill
(region, image, drawing, page), context, editable textarea with a 110-char
counter, source tag (Claude wrote / you edited / placeholder), Decorative tick.
Two extra sections whenever non-empty: "No picture available" and "Left alone"
(filename-only alt, read-only unless Replace anyway).

Prove compliance: header sentence with files passing and failing; honesty
callout when placeholders remain; census table by rule with files (not
occurrences), lane split, kind (mechanical, semantic, source re-export).

## 6. Assistant

Two columns (`1fr 300px`): quick-job chips that fill the composer, a
transcript (`role=log aria-live=polite`) with collapsed tool rows, an inline
Allow/Deny card, a composer (Enter sends, Shift+Enter newline, Send `.btn.ai`,
Stop, New conversation); a rail with session facts and the "What it changed" ledger.

The rail's first card is Student names: how many of the roster are swapped for
tags, what the last message swapped, and Re-read the roster. A send that names
two students at once comes back 409 with a sentence naming both, and the
composer keeps the text.

Allow/Deny card: headline by kind; **What:** the gate's own sentence (never the
model's description); "Claude describes it as" muted; "Why it asks"; on a call
that reads student data, one line saying the name swap does not cover what
Claude reads; the exact command in mono; Deny (focused by default) and Allow (`.btn.danger`, because it
writes); a countdown that resolves to Deny. Escape does nothing. Leaving the
view registers a dock pseudo-job so a pending card flips the chip to "needs you".

Polling: `GET /api/assistant/<cid>/events?since=<seq>` at 700 ms while open.

## 7. Tools

Due dates: a facts form (start, weeks, term, finals end, breaks, weekday, time;
anything not derivable tinted warn with "Tell me:"), then a table per week
(module, items, current due, proposed due editable, note) and Apply with the
detail list rendered by `askConfirm`.
Export: type, Export now (job), FERPA callout, result row with Open folder, history.
Import/clone: two mode cards (another course, an .imscc), destination (this
course or a new unpublished shell), populated-destination callout with "Add anyway",
Dry run counts, Import (confirm), then planned vs actual counts.
Trim nav: plan rows per tab; Back up a quiz: quiz select, confirm, link to the copy.

## 8. Build

Home with New cards and a Drafts table. Draft form: title, module and
position, points, group, publish (default unpublished), look, brief; Draft with
Claude (`.btn.ai`). Review: preview, scan chips (one h2, headings in order,
alt present, ASCII only, contrast), raw HTML, editable body (border turns
`--edit` once edited), Place in course (confirm). Manifest tab with dry run and
push (confirm); the module-wipe gate surfaces as a callout.

## 9. Visual system

Tokens added to `:root` and the dark block:

| Token | Light | Dark | Use |
|---|---|---|---|
| `--a11y` / `--a11y-soft` | `#0f766e` / `#e2f3f0` | `#6fd3c7` / `#14302c` | Accessibility |
| `--build` / `--build-soft` | `#8a4b2a` / `#f6ece5` | `#e0a37e` / `#2f2119` | Build |
| `--tools` / `--tools-soft` | `#4a5568` / `#eceff3` | `#a7b3c4` / `#23282f` | Tools |
| `--zone` / `--zone-soft` | set by `body[data-area]` | | tab underline, area head rule, one primary button |

Assistant uses `--ai`; Grade keeps `--accent`. Zone colour appears in exactly
three places per area. Navy is a fixed band fill (hub band, schedule band) with
white text; gold is a rule, never a fill or text on light surfaces. Fix the
dark-mode `.btn.primary` / `.btn.ai` text (`color: var(--bg)`).

Pills carry words, never colour alone. `.needCard` says what is missing, what
it enables, how to install, and has Check again -- plus Install it when the
tool reports `can_install`, which asks in the browser before running anything. Disabled verbs keep a title.

## 10. Files and load order

```
core.js       shared helpers, router, jobs, dock, area registry (from app.js 1-278 and 2416-2772)
grade.js      the rest of app.js, byte-identical except two crumb hrefs
hub.js  a11y.js  build.js  tools.js  assistant.js     each an IIFE exposing open<Area>() and calling registerArea
boot.js       boot();
viewer.js     (module, unchanged)
```

`registerArea({ id, label, zone, open(courseId, rest), badge(hubStatus) })`.
State stays in the global `S` under namespaced keys (`S.hub`, `S.a11y`, ...).
Areas register a poll-stopper on `S.onLeave`, which `showView` runs and clears.

## 11. Accessibility of the Studio itself

Skip link; focus moves to the view heading after route; global
`:focus-visible` outline in the zone colour with a positive offset; tabs with
real ARIA and arrow keys; dialogs trap focus and return it; `#status` is
`role=status`; a hidden `#live` node announces job state changes; captions and
`th scope` on tables; reduced-motion media query; contrast-checked tokens;
writes use full-size buttons.

## 12. Endpoints the UI assumes

| Area | Endpoints |
|---|---|
| Hub | `GET /api/courses/{cid}/hub`, `GET /api/courses/{cid}/ledger`, `POST /api/courses/{cid}/hub/refresh`; `/api/health` gains `tools` |
| Gateway | `GET /api/a11y/{cid}/{kind}/state`; `POST .../list`, `.../fetch`, `.../describe`, `.../fixes`, `.../push {apply, confirm}` |
| PDF | `GET /api/pdf/{cid}/state`; `GET /api/pdf/{cid}/picture?hash=`; `POST /api/pdf/{cid}/(fix|describe|upload|prove|backup|rollback)`; `POST /api/pdf/{cid}/alt` |
| Build | `POST /api/build/{cid}/draft`; `GET/POST /api/build/{cid}/drafts`; `POST /api/build/{cid}/place`; `GET /api/build/{cid}/manifest`; `POST /api/build/{cid}/manifest/push` |
| Tools | `GET /api/tools/{cid}/dates/plan`, `POST .../dates/apply`; `POST /api/tools/{cid}/export`; `POST /api/tools/{cid}/import`; `GET/POST /api/tools/{cid}/nav`; `POST /api/tools/{cid}/quiz-backup` |
| Assistant | `POST /api/assistant/{cid}/send`, `GET /api/assistant/{cid}/events?since=`, `POST /api/assistant/{cid}/answer`, `POST .../stop`, `POST .../new` |
| Batch | `POST /api/batch/a11y {course_ids, look, apply, confirm}` |
| Record | `GET /api/record/{cid}`, `GET .../file?month=`, `POST .../verify`, `POST .../sync`, `POST .../setting {to_canvas}` |
| Tools | `POST /api/tools/install {tools: [...]}` (a job; installs the optional binaries) |
| Assistant | `POST /api/assistant/{cid}/names/refresh` (re-read the roster for tagging) |

Long operations return `{job}`; confirm refusals keep the 409 shape
(`needs_confirm, summary, detail, confirm`) so `postConfirmed` and
`runJobConfirmed` work unmodified.
