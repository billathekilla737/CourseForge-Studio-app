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

## Remediating an existing course — the SCRIPTED pipeline (use this, don't improvise)
Three scripts ship the whole workflow (all in `scripts/`; validated end-to-end on MGCCC 2026-07):

1. **Dump** — `Dump-CanvasContent.ps1 [-CourseId <id>] [-WorkDir <dir>]`
   Downloads every body Ally scans (pages, assignment descriptions, discussion messages, quiz
   descriptions, syllabus) + writes `manifest.json`. Strips the auto-injected theme `<link>/<script>`.
   Skips quiz-/discussion-backed assignment SHELLS (their PUT 400s; the real body is on the quiz/topic).
2. **Transform** — `python restyle_html.py transform <WorkDir> --look hybrid|rich|clean`
   Deterministic (touches only `style=""` attrs + wraps unstructured bodies in hero+card; entity-encodes
   non-ASCII). **DEFAULT = hybrid** (filled navy hero+footer only, ~2 advisories/page). `rich` = all
   fills; `clean` = zero fills for max-score mandates. Prints the expected Ally advisory count.
   `python restyle_html.py scan <WorkDir>` reports the hard a11y issues (alt/headings/tables/links).
   **Image alt text still needs the agent's eyes** — view course-hosted images (see above) and fix alt
   in the styled files before verify.
3. **Verify** — `python restyle_html.py verify <WorkDir>` (exit 0 required)
   Proves per item: visible text byte-identical (styled) or verbatim-contained (wrapped), href/src sets
   unchanged, headings preserved, no dark-on-navy, pure ASCII, missing-heading fixed.
4. **Push** — `Push-CanvasRemediation.ps1 -WorkDir <dir> [-Apply]`
   In-place body updates ONLY (never modules/publish state/titles). Dry-run default. Refuses without a
   passing verify-report. Test-writes first item (403 = write-locked course, aborts clean). Live
   re-verify fetches every item back. Quiz descriptions go as JSON (see gotchas).
5. Have the instructor **re-scan in Ally**; advisory "use of color" items from fills are reviewed +
   marked resolved there (decorative navy).

## Operational gotchas (do not relearn)
- **Quiz descriptions IGNORE form-encoded PUTs** — HTTP 200, nothing saved (same family as the tabs
  API). Send JSON: `{"quiz":{"description":"..."}}`. Push-CanvasRemediation does this.
- **Quiz-/discussion-backed assignments 400 on `assignment[description]`** — they are shells; edit the
  quiz description / topic message instead. Dump-CanvasContent skips the shells automatically.
- **Never hand a hashtable body to PS 5.1 `Invoke-RestMethod` for big HTML PUTs** — its form serializer
  mis-encodes some bodies and Canvas drops the param and 200-no-ops (observed live: push "succeeded",
  content unchanged). Build the form body with `[uri]::EscapeDataString` (chunked; it throws on very
  long strings) or send JSON.
- **Raw 4-byte emoji in a body = Canvas API 500** (generic Apache error page). Entity-encode ALL
  non-ASCII before pushing (`restyle_html.py` asciify does this).
- **Canvas auto-injects the account theme `<link>/<script>` on READ** — strip before PUT or it gets
  stored and double-injected (dump does this).
- **PS 5.1 `Set-Content -Encoding UTF8` writes a BOM** that breaks `python json.load` — write manifests
  BOM-less (`[IO.File]::WriteAllText` + `UTF8Encoding($false)`) and read with `utf-8-sig`.
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
- **`.pptx` (modern): FULLY AUTOMATED** via the two-phase gateway — no per-file human oversight.
  `Remediate-CanvasPptx.ps1` + `remediate_pptx.py` (both in `scripts/`); requires `pip install python-pptx`.
  1. `-Action List` → enumerate the course's decks. `-Action Fetch` → download each (original kept as
     `original.pptx` backup) and auto-scan: extracts every image to `work\images\` + a `report.json`
     of issues (missing/filename/over-long alt, untitled slides, headerless tables, low-contrast).
  2. **Agent vision phase:** READ each extracted image, write `work\fixes.json` — concise alt (≤110
     chars) per image key, `""` for decorative (sets the real PowerPoint decorative flag), and a title
     per untitled slide. Slide context strings in the report help describe accurately.
  3. `-Action Push` → applies fixes (`fixed.pptx`), re-verifies to 0 hard issues, then uploads over the
     original (`on_duplicate=overwrite`, same name + folder, so **course links keep working**).
     Dry-run by default; `-Apply` to upload.
  - Untitled slides get the title placeholder filled, or a cloned/synthesized title positioned
    **off-canvas** — visuals unchanged, screen readers announce it (the Microsoft-documented technique).
  - **Contrast is REPORT-ONLY** (recoloring an instructor's design is their call) — surface the list.
  - Never touch **submission attachments** (student work) — course Files only.
  - Round-trip validated end-to-end (upload → download → re-verify = 0 hard issues) on MGCCC, 2026-07,
    including a worst-case deck (image-only slides, statements baked into pixels: alt must carry the
    statement text AND the artwork content, since a screen reader gets nothing else).
  - **Overwrite changes the file id** (name preserved; Canvas keeps replacement pointers so module/page
    links generally redirect) — after pushing to a real course, click a module link to the file to confirm.
  - **Image-only text decks:** the durable fix is rebuilding as real text slides (also helps zoom users) —
    that changes the design, so offer it to the instructor; the alt-text pass is the non-invasive fix.
- **`.ppt` (legacy):** `python-pptx` **cannot** read it; convert to `.pptx` first (PowerPoint/LibreOffice)
  — not headless-automatable here.
- **`.docx`: FULLY AUTOMATED** via the same two-phase gateway — `Remediate-CanvasDocx.ps1` +
  `remediate_docx.py` (needs `python-docx`). Scan finds missing/filename image alt, documents with no
  real Heading styles (plus faux-bold heading CANDIDATES with paragraph indexes), and tables without a
  repeating header row; the agent views extracted images and writes `fixes.json` (alt text; **opt-in**
  paragraph→Heading promotions — they restyle the text, so never auto-apply; `table_headers`); Push
  re-verifies to 0 issues and overwrite-uploads (original kept). Honest scope: alt + headings + table
  header rows — not full document tagging.
- **`.pdf`: TRIAGE ONLY** — `Triage-CanvasPdfs.ps1` + `triage_pdf.py` (needs `pypdf`) classifies every
  course PDF: `scanned-image` (no text layer → OCR/re-source; the worst Ally offenders), `text-untagged`
  (words but no headings/reading order), `tagged` (spot-check quality), `encrypted`. Real remediation
  (tagging, reading order) stays **manual** (Acrobat) — never claim otherwise. The ranked
  `triage-report.md` tells the instructor which of their 40 PDFs actually hurt the score.
- **Batch across courses:** `Batch-Remediate.ps1` runs the whole HTML pipeline over `-CourseIds`
  with one aggregate summary — dry-run first, always.
- **Publisher decks (Pearson/Cengage):** edits may revert on the consortium's next import — the
  instructor's **own** files are the durable win; do those first.
