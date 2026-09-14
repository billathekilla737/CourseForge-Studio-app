# Test plan

How to prove CourseForge Studio works, end to end, without changing a live
course. Written so that a person, or a model working on their behalf, can run
it top to bottom and say at the end either "everything passed" or exactly which
numbered step failed and what it showed instead.

Read the whole of section 1 before doing anything. Then work through sections
2 to 10 in order. Every step says what to do, what you should see, and what a
failure looks like. A step marked **WRITE** changes Canvas if you go through
with it: you stop at the confirmation dialog and press Cancel. Nothing in this
plan requires clicking past one.

---

## 1. Ground rules

**Nothing you do here may reach Canvas except through the confirmation
dialog, and you never confirm.** The whole design of the Studio is that every
Canvas write is refused once and shown to you as a sentence. Testing that the
sentence appears is the test. Sending it is not.

Two writes happen without a dialog and are expected. Both go to the
instructor's own Canvas *user files*, never to a course or a student:

- Opening an assignment can upload a small "handoff" envelope (grading state
  carried between machines). It is refused when `allow_canvas_writes` is false.
- The record of actions is copied to `Files / courseforge-studio/record` every
  few minutes when that setting is on.

**Run the plan with the hard lock on if you can.** Open `config.json`, set
`"allow_canvas_writes": false`, restart the server. Every write path is then
refused by the server itself with a sentence that says so, and the plan below
still works because every step stops before the write. Put it back to `true`
when you are done.

**Do not type student data into a shell.** If you use a terminal to probe
routes, the responses may contain names; that is fine. Just never put a name
on the command line.

**Report faithfully.** A step that shows something other than what is written
here is a failure even if the page looks fine. Copy the exact text you saw.
Do not mark a step passed that you did not do.

**What you need:** Python 3.10+, the Claude Code CLI signed in, a Canvas token
saved (the setup screen), at least one course of your own in the current term.
The examples below use course `734975` and assignment `15977675`; substitute
your own ids from `/api/picker`.

---

## 2. Preflight (terminal, five minutes)

Run from the repository root. Each must pass before the browser part starts.

| # | Command | Expect | Fail if |
|---|---|---|---|
| 2.1 | `python -m compileall -q courseforge` | prints nothing, exit 0 | any `SyntaxError` |
| 2.2 | `python -m unittest discover tests` | last lines `Ran N tests` then `OK` | `FAILED`, or any `unexpected success` |
| 2.3 | `python -m courseforge doctor` | ends `All good.`; Canvas login OK; Claude login OK; every area listed after `Areas :` | `Some checks failed`; an area named as FAILED to load |
| 2.4 | `python -m courseforge --help` | subcommands include `serve gui doctor courses tools students record a11y docs pdf content course assistant` | a missing area verb |
| 2.5 | `python -m courseforge a11y --help`, then the same for `docs`, `pdf`, `content`, `course`, `assistant` | each exits 0 with usage text | traceback |
| 2.6 | `python -m courseforge record --course account --verify` | `Chain : UNBROKEN`, exit 0 | `BROKEN`, exit 2 |
| 2.7 | `python -m courseforge tools` | every optional tool listed, OK or with an install line | traceback |
| 2.8 | `python courseforge/pdf/fastlane.py selftest` | `SELFTEST PASS` (an OCR lane may say skipped when Tesseract is absent) | `FAIL` |

Then start the server from a plain terminal, not from inside a Claude Code
session:

```bash
python -m courseforge serve
```

It prints the port. The default is `8900`; the examples below assume it.
Confirm with `curl http://127.0.0.1:8900/api/health` (a JSON object, and the
word `token` does not appear anywhere in it).

**Make sure it is the only one.** Windows lets a second copy bind the same
port, and a copy left running from yesterday answers with yesterday's code.
Run `netstat -ano | findstr :8900 | findstr LISTEN` and expect exactly one
line. If there are two, stop the older process (Task Manager, or
`Stop-Process -Id <pid>`) and check again. Every result in this plan is
meaningless if two servers are answering.

---

## 3. Safety spine (terminal, ten minutes)

These prove the doors are locked before you touch the rooms. PowerShell shown;
`curl.exe` works the same on any shell.

| # | Do | Expect |
|---|---|---|
| 3.1 | `Invoke-RestMethod http://127.0.0.1:8900/api/setup` | `token_len` is a number; no token value in the output |
| 3.2 | `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8900/api/settings` | `404` (settings are POST only) |
| 3.3 | `curl.exe -s -D - -o NUL http://127.0.0.1:8900/api/health` | headers include `X-App-Build` and `Cache-Control: no-store` |
| 3.4 | `curl.exe -s -o NUL -w "%{http_code}" --path-as-is http://127.0.0.1:8900/../config.json` and again with `/..%2f..%2fconfig.json` | `404` both times |
| 3.5 | `curl.exe -s -w " %{http_code}" --path-as-is "http://127.0.0.1:8900/api/a/../probe/file?name=x"` | `400` or `404`, and no folder named `probe` appears beside `data\` |
| 3.6 | `curl.exe -s -o NUL -w "%{http_code}" -X OPTIONS http://127.0.0.1:8900/api/health` | `501` |
| 3.7 | `Invoke-WebRequest -Method POST -Uri http://127.0.0.1:8900/api/settings -Headers @{Origin="https://evil.example"} -ContentType application/json -Body '{}' -SkipHttpErrorCheck` | `403` (a foreign origin cannot POST) |
| 3.8 | Same POST with `-ContentType application/x-www-form-urlencoded` and `Origin=http://127.0.0.1:8900` | `403` (form posts are refused) |
| 3.9 | `$j = Invoke-RestMethod -Method POST http://127.0.0.1:8900/api/tools/734975/nav -ContentType application/json -Body '{"apply":true}'` then poll `Invoke-RestMethod http://127.0.0.1:8900/api/jobs/$($j.job)` until `state` is not `running` | `state = error`, `needs_confirm = true`, a `summary` sentence and a `confirm` token. **Do not send that token anywhere.** With the hard lock on, the error instead says writes are locked off in config.json. |
| 3.10 | Repeat 3.9 with body `{"apply":true,"confirm":"0000000000000000"}` | the job ends in error saying the confirmation is not valid or has expired; `GET /api/tools/734975/nav` shows the same plan as before |
| 3.11 | `python -c "from courseforge import canvas_policy as p; p.check_scope('content','GET','/api/v1/courses/1/enrollments')"` | raises `PolicyDenied` |
| 3.12 | Same with `/api/v1/courses/1/sections?include[]=students`, `/api/v1/courses/1/discussion_topics/2/view`, `/api/v1/users/self/activity_stream`, `/api/v1/courses/1/%75sers` | each raises `PolicyDenied` |
| 3.13 | Same with `/api/v1/courses/1/pages/welcome`, `/api/v1/courses/1/tabs/home`, `/api/v1/users/self/files` | each returns without raising |
| 3.14 | `python -m courseforge a11y push --course 734975` (no `--apply`) | prints that it is a dry run and nothing was written |
| 3.15 | `python -m courseforge a11y push --course 734975 --apply < NUL` (stdin closed) | `Not applied.`; nothing written |
| 3.16 | Open `courseforge/claude_cli.py` and find the `claude -p` command line | it carries `--tools ""`, `--strict-mcp-config` and `--setting-sources user`; `STRIP_ENV` lists `CANVAS_TOKEN` and `ANTHROPIC_API_KEY` |

---

## 4. Shell, course list and hub (browser)

Open `http://127.0.0.1:8900/`. Keep the browser console open (F12) for the
whole run; any red line that is not a `404` for a missing optional file is a
failure to report.

| # | Do | Expect |
|---|---|---|
| 4.1 | Land on `#/` | tab title `CourseForge Studio`; header shows your name and Canvas host; a **Term schedule** and **Canvas token…** button; an **Inbox** chip with a count |
| 4.2 | Look at the page | a "Where you left off" band (if you have graded before), the course list for the current term with a term dropdown, and an "Across your courses" list on the right ending with **Reports** |
| 4.3 | Change the term dropdown to another term and back | the list re-filters each time; no console error |
| 4.4 | Click **Canvas token…** | a dialog with the Canvas address filled in and an empty password field; a line saying a token is already saved with its length. The token itself is never shown. Cancel. |
| 4.5 | Click a course | `#/c/<cid>`; the tab title becomes the course name; an area bar with Grade, Accessibility, Build, Tools, Assistant, Record; a stat strip; six cards; "What changed in this course" |
| 4.6 | Compare the Grade badge in the area bar with the "Waiting to grade" stat | equal |
| 4.7 | Look at the Tools tab in the area bar | no badge (a due-date forecast is not work waiting) |
| 4.8 | Click **Refresh from Canvas** on the hub | a progress dialog, then it closes itself and the stats repaint |
| 4.9 | Click **Courses** in the breadcrumb | back on `#/`; tab title is `CourseForge Studio` again, not the course name |
| 4.10 | Press Tab once from the address bar and press Enter on **Skip to content** | focus lands on the page heading of whatever view is up, including on Inbox and Reports |
| 4.11 | Press Ctrl+Shift+R to hard-reload while on the hub | the same hub, no "This page is out of date" bar |
| 4.12 | Edit any `.js` file on disk (add a blank line) and click anything that calls the server | the yellow "This page is out of date" bar appears with a Reload button. Undo your edit. |

---

## 5. Grade

Use an assignment that has submissions and a rubric.

| # | Do | Expect |
|---|---|---|
| 5.1 | Area bar **Grade** | the assignment list with Due, To grade, Points, Rubric, State columns |
| 5.2 | Click an assignment | `#/c/<cid>/a/<aid>`; the roster on the left, the rubric in the middle, the work pane; status settles on something like "in step with Canvas" within a few seconds |
| 5.3 | Console → Network | on arrival the page POSTs `.../sync` and `.../pull` once. Nothing else is POSTed until you act. |
| 5.4 | Click a student, move one rubric slider | after a second the status says saved; the big score and the roster number for that student agree (if the student carries a curve, both include it) |
| 5.5 | Filter chips: All, Turned in, Needs review, Edited, Ungraded, Missing, Conflicts | each changes the list; reload keeps the chosen chip |
| 5.6 | Keys `j` / `k`, then `w` | selection moves down and up; the work pane toggles |
| 5.7 | Ctrl-click two students | bulk bar shows "2 selected"; the re-grade button names 2 students. Do not click it (it costs a model call). |
| 5.8 | **View assignment**, then **Announcement…** without ever having opened the schedule this session | the announcement dialog opens; no console error. Close it. |
| 5.9 | **Insights** then **Curve grades…** | a preview within a second; press **Apply curve**: the Studio's own dialog "Apply this curve?" appears, not a browser popup. Cancel; the curve dialog returns with working buttons. |
| 5.10 | **Push to Canvas…** | the plan fills in ("will write N, held back M"). **WRITE**: press the red button once; the confirmation quotes the server sentence, which names the count and says the assignment is set to manual posting first. **Cancel.** Status says nothing was sent. |
| 5.11 | **Make live…** | lists hidden and visible grades. Cancel. |
| 5.12 | **Export** | status "exported N rows"; `data/<cid>/<aid>/grades.csv` exists |
| 5.13 | **Instructions**: type a sentence, Save | the button gains a dot; `data/<cid>/<aid>/instructions.md` holds the text |
| 5.14 | Header **Term schedule** | every dated item across the term grouped by week; footer says "Canvas synced N minutes ago" (not weeks); the current week is marked |
| 5.15 | Schedule: click an item title | a reader with the description and rubric; **Announcement…** opens a dialog. Close without drafting. |
| 5.16 | Schedule: **Tell it what to change…** | a dialog with a text box. Type `move everything in week 3 one day later` and run the plan. A plan appears; **WRITE**: applying it asks first. Cancel. |
| 5.17 | Schedule: **Accommodations: apply all…** | the standing roster appears with what would be applied. **WRITE**: applying asks first. Cancel. |
| 5.18 | Verify nothing reached Canvas | `GET /api/a/<cid>/<aid>` shows no new `pushed_at` on any student compared with before 5.2; `GET /api/courses/<cid>/ledger` has no new row |

---

## 6. Accessibility

Use a course that already has local data if you have one (`data/<cid>/pdf/`
or `data/<cid>/docs/` exists). Fetching and scanning downloads copies and is
slow but harmless.

| # | Do | Expect |
|---|---|---|
| 6.1 | Area bar **Accessibility** | seven tabs in two groups: **Fix accessibility** (Pages, PowerPoint, Word, PDFs, PDF triage) and **Find and replace** (in PDFs, in Office files). The same seven on every tab. |
| 6.2 | **Pages** | the five-step List, Scan, Review, Dry run, Apply strip; verbs "Bold used as structure", "Bordered boxes", "Restore previous bodies"; an empty state if nothing is fetched |
| 6.3 | **Refresh list** (reads names only) then **Fetch & scan** | a job that names each page as it goes; the count of items and hard issues; nothing pushed |
| 6.4 | Step 3 **Review**, pick a look (Clean, Hybrid, Rich) and **Transform** | a job; the review shows before and after for an item |
| 6.5 | Step 4 **Dry run** | "Will be pushed (N)" and "Will not be pushed" lists are populated; the Apply button is enabled when N is above zero |
| 6.6 | **WRITE**: step 5 Apply, once | the confirmation quotes a sentence naming pages, assignments and discussions and says visible text is verified unchanged; a per-item list with sizes, no `[object Object]`. **Cancel.** |
| 6.7 | **Restore previous bodies** on a course never pushed from here | a status error saying nothing has been pushed; no dialog |
| 6.8 | **PowerPoint** | blurb about alt text, slide titles and header rows; the gateway; a file selector and a grid of pictures with editable descriptions and a 110-character counter once something is scanned |
| 6.9 | Type in a description and clear it | status says saved here, nothing uploaded |
| 6.10 | **Dry run** then **WRITE** Apply once | confirmation with the upload sentence and per-file names and sizes. **Cancel.** |
| 6.11 | **Word** | the same as 6.8 with Word wording |
| 6.12 | **PDFs** | verbs Refresh file list, Back up & fix, Describe images, Upload, Prove compliance, Back up only, Roll back; a stat strip; a table with lane, state, verify and compliance columns; sub-tabs Files, Alt text, Prove compliance |
| 6.13 | Filter chips on the PDF table | each narrows the table; counts are not all zero when files exist |
| 6.14 | **Upload** | a dry-run job, then **WRITE**: the confirmation lists files with sizes. **Cancel.** Same for **Roll back**. |
| 6.15 | `#/c/<cid>/pdf` typed directly | the same PDF screen with the seven tabs and PDFs held down |
| 6.16 | **PDF triage** | a ranked table or an empty state; Dry run and Apply disabled with a title explaining why |
| 6.17 | **in PDFs**: type a word in "Look for" and press Enter | a scan job; status says scanned, nothing changed; matches or "no matches" per file |
| 6.18 | **in Office files** | the same for .docx, .pptx, .xlsx |
| 6.19 | From `#/`, **Batch Course Restyle** | term select, course cards, look cards with **Example** buttons; Dry run with nothing picked says pick a course first |
| 6.20 | From `#/`, **ADA file compliance** | course cards, three file-kind cards, verbs Survey, Scan and repair, Describe, Upload. Pick one course and PDFs, **Survey**: the table shows real counts and a "ready" pill where files are fixed. |
| 6.21 | **Upload the fixed files** when a file still has a placeholder description | the Studio's own dialog "Some pictures are still on a placeholder" with Go back. Go back. If you continue once instead, **WRITE**: the server confirmation appears. **Cancel.** |
| 6.22 | `GET /api/courses/<cid>/ledger` | unchanged from before section 6 |

---

## 7. Build and Tools

| # | Do | Expect |
|---|---|---|
| 7.1 | Area bar **Build** | crumb names *this* course; tabs Drafts, Manifest, Rubrics; six "Something new" cards; an empty Drafts state |
| 7.2 | Click **Page** | a form; the Module box becomes a select within two seconds listing modules with "(unpublished)" where true; an assignment-group select |
| 7.3 | Submit with an empty Title | status "give it a title first"; focus on Title; no job |
| 7.4 | (Optional, costs a model call) Fill it in and **Draft with Claude** | a job, then `#/c/<cid>/build/draft/<id>` with the draft, style-check chips, an editable body |
| 7.5 | Edit a character, **Save** | status "saved here and re-checked" |
| 7.6 | **Place in Canvas** | **WRITE**: the confirmation names the module and says it is unpublished. **Cancel.** |
| 7.7 | **Delete this draft** | the Studio's own dialog, saying Canvas is not touched. Confirm; back on Drafts. |
| 7.8 | **Manifest** with no file | an empty state naming `data/<cid>/build/manifest.json` |
| 7.9 | Create that file with `{"mode":"project","syllabus_html":"<div><h2>Course Syllabus</h2><p>x</p></div>"}` and reload the tab | stat strip says project; ticks Publish, Leave the modules alone, Rebuild the modules |
| 7.10 | **Dry run** with neither module tick | a red callout that the modules are not rebuilt and nothing is pushed until one is ticked |
| 7.11 | Tick **Leave the modules alone**, **Dry run** | a plain callout that modules are left untouched; one row for the syllabus. **WRITE**: Push asks first. Cancel. Delete the manifest file. |
| 7.12 | **Rubrics** with no file | an empty state naming `rubrics.json` |
| 7.13 | Area bar **Tools** | tabs Due dates, Export, Import, Clone, Trim nav, Back up a quiz, Outcomes |
| 7.14 | **Due dates** | facts filled with their sources (course start, week count from modules, term, finals end, breaks); a week table with Due now and a Proposed due input per week; notes such as "moved off a holiday" and "final: due on the last day of finals" |
| 7.15 | Set Due weekday to **Sunday**, **Compute the week table** | every proposed date is a Sunday; no false "moved off a holiday" notes. Set it back to Monday and recompute. |
| 7.16 | Edit one proposed date by a day | the Apply button enables and says the dates are as edited. **WRITE**: Apply asks first with a week-by-week list. Cancel. |
| 7.17 | **Export** | the FERPA callout, the folder path, an empty table. Do not click Export now: it creates an export object in Canvas (allowed, ledgered, but not needed here). |
| 7.18 | **Import**: a cartridge file at `C:\nope.imscc`, Dry run | a job error containing "file not found" |
| 7.19 | **Import**: another Canvas course, source = this course, Dry run | an error that source and destination are the same |
| 7.20 | **Import**: a different real course as source, Dry run | counts From and Into, and if this course has content, a refusal that it already has content with an "Add anyway" tick; Import disabled |
| 7.21 | **Clone**, Dry run | a plan or a refusal asking for the account; **WRITE**: Create the shell asks first. Cancel. |
| 7.22 | **Trim nav** | a sentence of visible tabs starting with Home; a table with the Settings row marked never touched; Apply shows the change count and is disabled at zero. **WRITE**: Apply asks first. Cancel. |
| 7.23 | **Back up a quiz** | quizzes listed with question counts and published state; none named BACKUP. **WRITE**: Back it up asks first. Cancel. |
| 7.24 | **Outcomes** | four step chips; program radios or a resolved program; Fetch reads the framework (read); Judge costs a model call; **WRITE** Add it to the course asks first. |
| 7.25 | `GET /api/courses/<cid>/ledger` | unchanged from before section 7 |

---

## 8. Assistant, Inbox, Record, Reports

| # | Do | Expect |
|---|---|---|
| 8.1 | Area bar **Assistant** | starter chips; a transcript pane; a rail saying how many students are swapped for tags; "Nothing reaches Canvas without an Allow" |
| 8.2 | Type `@` in the box | a roster picker; arrows move; Escape closes; Enter inserts a name and the status says which tag it goes out as |
| 8.3 | Type a real student's name misspelt by one letter, press Enter | an inline card saying nothing was sent, offering **Use <name>**, **Send as typed**, **Let me edit it**. Choose edit. |
| 8.4 | Send `Dry run the accessibility restyle for this course and stop before applying.` | your message, Claude's reply streaming, collapsed steps; no Allow card for a dry run |
| 8.5 | Open `data/<cid>/assistant/conversation.txt`, `events.jsonl`, `system-prompt.md` in an editor | no real student name anywhere; tags only |
| 8.6 | Send `Now apply it.` | an Allow / Deny card headed that Claude wants to change the live course, with the exact command containing `--apply` and a countdown. **Deny.** The transcript says you denied it. |
| 8.7 | Leave the Assistant for the hub; watch Network | polling of `/events` stops within one tick |
| 8.8 | **New conversation** | the Studio's own dialog; on Yes the transcript clears and the old file is rotated |
| 8.9 | `python -m courseforge students list --course <cid>` | JSON with `tag` rows and no `name` field; search the output for a roster name: none |
| 8.10 | Header **Inbox** | a thread list; the hint says reading a thread never marks it read in Canvas; chips Inbox, Unread, Archived, Sent |
| 8.11 | Open a thread; Network | a single GET; no POST. In Canvas the thread stays unread. |
| 8.12 | **Draft a reply with Claude**, leave the box empty, **Draft it** | a job whose log says no name is in what goes out; a draft appears. **Discard.** |
| 8.13 | **Write it myself**, type a line | **WRITE**: Send asks first with the recipient and text. **Cancel.** |
| 8.14 | Go to `#/inbox`, then immediately to `#/reports` | Reports paints and stays; no console TypeError |
| 8.15 | Area bar **Record** | "N entries, unbroken"; where it is kept on this PC and in Canvas; Student, Part of the Studio and Month filters |
| 8.16 | **Check the record** | status says it checks out |
| 8.17 | **Stop keeping it in Canvas** | the Studio's own dialog. Cancel; the switch is unchanged. |
| 8.18 | Type `#/c/account/record` | the account-wide record with at least the accommodation roster if you have one |
| 8.19 | **Download this month** | a `.jsonl` for the most recent month; every line has `prev` and `hash` |
| 8.20 | Tamper test on a copy: back up `data/audit/<month>.jsonl`, delete its last line, run `python -m courseforge record --verify` | `BROKEN`, says the last entry was removed, exit 2. Restore the file; `UNBROKEN`. |
| 8.21 | **Reports** from `#/` | tabs Accessibility score and Syllabus policies; a course picker defaulting to the current term with All and None |
| 8.22 | On `#/`, set the term dropdown to **All terms**, then open Reports | the picker still shows the current term's courses, not zero |
| 8.23 | **Syllabus policies**, Run | a matrix of ticks; a course with no syllabus has a "no syllabus" pill and one line of its own at the top of "What to fix first"; the per-statement lines do not count it |
| 8.24 | **Accessibility score**, Run | a table of Before and Now per course; a course never scanned reads "never looked at" or "N files, not scanned yet"; a callout offers "Scan N courses now"; if a course's forecast fails, it is named under the table and the others still score |
| 8.25 | Click **Scan N courses now** (downloads copies; uploads nothing) | the scan runs, the score re-runs, the table fills in and the callout goes |

---

## 9. Nothing reached Canvas

At the end, for every course you touched:

1. `GET /api/courses/<cid>/ledger` shows the same rows it did before you began.
2. `python -m courseforge record --course <cid> --verify` says `UNBROKEN` and lists no entry from today that you did not expect (with the hard lock on, none).
3. In Canvas itself: the gradebook history for the assignment in section 5 has nothing from today; the pages you restyled still have their old bodies; no new export, import or announcement exists.

If any of these show a change, stop and report it with the step number that produced it. That is a bug in the gate, and it outranks everything else in this plan.

---

## 10. Reporting

Write the result as a list of step numbers that failed, each with what you did
and the exact text you saw, then one line: `N of M steps passed`. A run with
no failures is reported as `All steps passed` with the date, the commit
(`git rev-parse --short HEAD`), the Python version and whether the hard lock
was on.

Do not fix anything while running the plan. Finish the run, report, and fix
afterwards, so the report describes one version of the code.

---

## Appendix: read-only routes for a terminal-only check

Every one of these is a GET that changes nothing and should answer 200 in
under three seconds (the docs `state` routes take under a second; the due-date
plan can take a few seconds the first time).

```
/api/health            /api/picker             /api/courses            /api/terms
/api/storage           /api/schedule           /api/claude-check       /api/inbox/unread
/api/inbox             /api/courses/<cid>/hub  /api/courses/<cid>/ledger
/api/courses/<cid>/assignments                 /api/a/<cid>/<aid>
/api/a11y/looks        /api/a11y/<cid>/html/state                       /api/a11y/<cid>/pptx/state
/api/a11y/<cid>/docx/state                     /api/a11y/<cid>/pdf-text/state
/api/a11y/<cid>/office-text/state              /api/a11y/<cid>/triage/report
/api/pdf/<cid>/state   /api/batch/files        /api/batch/a11y
/api/build/<cid>/state /api/build/<cid>/drafts /api/build/<cid>/modules /api/build/<cid>/groups
/api/build/<cid>/manifest                      /api/build/<cid>/rubrics
/api/tools/<cid>/state /api/tools/<cid>/nav    /api/tools/<cid>/quizzes
/api/tools/<cid>/dates/plan?tz=300&tzname=America/Chicago              /api/tools/<cid>/slo/state
/api/assistant/<cid>/state                     /api/record/<cid>
/api/reports/score/<cid>                       /api/reports/policies/<cid>
```

`/api/inbox?scope=` accepts only an empty value, `unread`, `archived` or `sent`;
anything else is a 400 by design.

## Deadline extensions

A student was ill or bereaved and needs longer on the work that fell during the
absence, in every course they are in. Reached from the picker, under *Across
your courses*, or at `#/extensions`.

Nothing here writes to Canvas until step 8. Steps 1 to 7 are safe on a live
course.

1. Open `#/extensions`. The tab should say **Deadline extensions**, and the
   student list should fill within a few seconds. Each student shows the
   courses Canvas has them in.
2. Type part of a name in the search box. The list narrows **and the box keeps
   focus** — you can keep typing without clicking back into it.
3. Click a student. A chip appears above the search box, the row highlights,
   and the button at the bottom enables and counts them.
4. Set the absence window to a stretch when work was actually due, leave
   *Extend by* at 3, and leave *Which courses* on the term.
5. Press **Show me what would move**. The job log names each course it reads
   and how many items were due in the window. Expect one line per course.
6. Read the dry run. Check, on at least one row:
   * **Due now** is the date the student really has, not always the class date;
   * **Would become** is exactly that date plus the days, with the *same
     time of day*;
   * **Measured from** says where the starting date came from.
7. Check the callout naming the timezone the dates were worked out in. On a
   machine without the `tzdata` package it will say *this PC* and explain why.
   If the course is in a different timezone from the machine, that sentence
   matters — install `tzdata` before trusting the dates.
8. Untick a row. The count on the button drops. **This is the check that
   matters**: the confirmation you get in step 9 must name the number you see
   here, not the original number.
9. Press **Move N dates in Canvas…**. It is refused once, and a dialog lists
   each change as *was → becomes*. Cancel it. Nothing should have changed in
   Canvas.
10. Press it again and confirm. Then, in Canvas, open one of those assignments
    and look at its dates: there should be an override titled
    **Extension: <student name>** for that student alone, and the class date
    should be untouched.
11. Open **Record** on that course. There should be one line per date moved,
    naming the student and both dates.
12. Run the same extension again with the same days. The second run should
    *update* the override it made rather than adding a second one — the student
    ends up with one override, further out, not two.

Expected refusals, each of which should be a readable sentence rather than an
error:

* a student who is in none of the courses in scope — the page should name the
  courses Canvas *does* have them in, and suggest ticking courses by hand;
* an assignment whose date for that student comes from an override shared with
  other students — left alone, and said so;
* an assignment with no due date for them at all;
* 0 days, or more than 90.
