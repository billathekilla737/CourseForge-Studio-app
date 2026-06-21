# ADA remediation playbook (Anthology Ally)

How to take an existing Canvas course's accessibility score up — learned end-to-end on MGCCC
courses (Ally scanner). Use this when an instructor says "make my course ADA compliant", "fix my
Ally score", or hands you an Ally issue export (CSV: Title, Type, Issue). Pair with
`style-guide.md` (the component library + the two looks).

## The scanner
MGCCC uses **Anthology Ally**, which gives a course a 0–100% score. It scans **HTML** (pages,
assignments, quiz/discussion bodies, the syllabus) **and documents** (PPTX, PDF, DOCX). Each issue
lowers the score by severity. "Potential …" issues are **advisory** (instructor can review/mark
resolved) but still pull the number down until cleared.

## What Ally flags, and how to clear each (HTML)
| Ally issue string | Real cause | Fix |
|---|---|---|
| Insufficient text color contrast | text < 4.5:1 on its background; also `rgba()` fills it can't composite | use the tested palette (all ≥4.5:1); solid backgrounds, never `rgba` |
| **Potential use of color alone** | (a) **any `background:` fill** — even neutral gray; (b) chromatic **non-heading** body text; (c) links distinguished by color only | remove fills, default-black body text, underline links. **Navy headings + borders of any color are EXEMPT.** |
| Headings may be missing / Styles instead of semantic markup / Page contains skipped headings | bold/large text faking a heading; no real `<h2>/<h3>`; level jumps | one real `<h2>` (title) then `<h3>` sections, no skipped levels; sub-labels = bold `<div><strong>` |
| Alternative text uses filename / image missing alt | `alt="img123.png"` or no `alt` | descriptive `alt` (see below); decorative → `alt=""` |
| **Alternative text is too lengthy** | alt is a paragraph | keep alt **concise (≤ ~110 chars)** — one short phrase, not a transcript |
| Table does not include header rows / missing scope | data table without `<th scope>` | real data table → add `<th scope="col">`/`<th scope="row">`; layout table → label/value rows |
| Link does not contain text | empty/icon-only `<a>` | put descriptive text inside the link |
| Linked or embedded external content… | a linked PDF / publisher site / embedded video | **manual** — about the external resource, not your HTML; surface to instructor |

## Two looks and the score trade-off (decide up front)
- **RICH** (filled navy bands, colored callouts) looks best but raises Ally's *advisory* "use of color"
  on **every filled element** — roughly **~12 per page**. Across a 20-page course that's hundreds of
  advisory flags; you will **not** reach a high score with filled bands.
- **CLEAN** (no `background:` fills; color only in **navy headings + borders**; default-black body;
  underlined links) scores **0** on use-of-color.
- **For a target like ≥90%, use CLEAN.** Confirm the trade with the instructor; many like the filled
  look until they see it caps the score. Both looks must still pass the *hard* checks above.

## Generating alt text (you can usually SEE the images)
- An `<img src>` whose URL contains the **course's own id** and a **`?verifier=<hash>`** token is
  downloadable: `curl -s -o tmp.png -L "<src, &amp; → &>"`, then read the PNG and describe it.
- An image hosted in a **different** course (a publisher/master id) usually returns **HTTP 403** —
  can't view; write alt from the surrounding text.
- **Concise** always (≤ ~110 chars). Decorative → `alt=""`.
- **Many-image gallery pages** (a deck rendered as 40–80 slide images): viewing every one is too slow;
  use **positional alt** ("Access Chapter 1 — slide graphic 5") — it clears the filename/missing flag
  fast and losslessly. Note to the instructor it's positional, not described.

## Remediating an existing course — the workflow
1. **Dump** every body locally (pages, assignments, quiz descriptions, **syllabus tab**).
2. **Scan / flag** the items that actually have issues (from the Ally CSV, or detect directly:
   filename/empty alt, substantial body with no heading, table without scope, empty link, fills).
   Skip already-clean and very short pages.
3. **Restyle** each flagged item into the template (one parallel agent per item for scale): rebuild in
   the CLEAN look, **view in-course images** for concise alt, add semantic headings, fix tables/links —
   and **preserve all instructional content verbatim** (restructure + fix a11y only; never rewrite or
   summarize, especially imported/publisher content).
4. **Verify** before pushing: 0 `background:` fills, navy only on `<h2>/<h3>`, no filename/empty/over-long
   alt, no banned tags, and **visible text unchanged** vs source (no content drift).
5. **Push** idempotently (pages → `wiki_page[body]`, assignments → `assignment[description]`,
   quizzes → `quiz[description]`, syllabus → `course[syllabus_body]`). **Never change publish state.**
6. Have the instructor **re-scan** to confirm; iterate on anything left.

## Operational gotchas (do not relearn)
- **Test-write before bulk-pushing a past course.** Some concluded courses are **write-locked**: a PUT
  returns **403** on *all* content (even a no-op page edit) despite `workflow_state: available` — usually
  a **closed grading period / concluded-with-restrictions**. The instructor/registrar must re-open it.
- **Published duplicate:** Canvas auto-appends `-2` to a duplicate-title slug, so a course can hold two
  same-titled pages (one published, one not). Restyle the **published** one; flag the dup for deletion.
- **Canvas keeps real `<table>`s** on API write (the "no layout table" rule is a house mobile preference,
  not a sanitizer limit). The sanitizer DOES strip styling `class=`, `box-shadow`, `<style>` (a Canvas
  file-link's own `class="instructure_file_link"` survives and is functional — keep it).
- **PS 5.1:** read/write bodies as explicit UTF-8 (`[IO.File]::ReadAllText/WriteAllText` with
  `UTF8Encoding($false)`) or you bake in mojibake; keep `.ps1` pure ASCII (em dashes break the parser);
  pass array args with the call operator, not `-File … -X a,b,c`.

## Documents (PPTX / PDF / DOCX) — Ally scans these too
Heavier and slower than HTML; set expectations before promising the whole set:
- **`.pptx` (modern):** remediable with `python-pptx` — add image **alt** (view each image) and ensure
  every slide has a **title**, then **re-upload** (replaces the file). Slow per deck (many images) and
  the round-trip should be validated on one deck first.
- **`.ppt` (legacy):** `python-pptx` **cannot** read it; convert to `.pptx` first (PowerPoint/LibreOffice)
  — not headless-automatable here.
- **`.pdf`:** real remediation (tagging, reading order, alt) is largely **manual** (Acrobat).
- **Publisher decks (Pearson/Cengage):** edits may revert on the consortium's next import — the
  instructor's **own** files are the durable win; do those first.
