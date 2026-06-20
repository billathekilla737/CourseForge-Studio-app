# Canvas-safe HTML style guide (MGCCC blue-and-gold) + accessibility

Inline-styled components that survive the Canvas Rich Content Editor sanitizer and meet
Anthology Ally's accessibility checks. Colors are the MGCCC "Mississippi" blue-and-gold
palette — swap the hexes for another school, but keep the *structure*.

## Two looks (pick per instructor preference)
The components below are given in the **RICH** form (the filled navy-banner look most
instructors want). There is also a **CLEAN** variant that scores a perfect Ally scan.

- **RICH (default):** filled navy hero + footer, colored callout fills, gold borders.
  Looks like a designed page. **Trade-off:** Ally raises *"Potential use of color alone"*
  on the filled bands — that is an **advisory** 1.4.1 item the instructor reviews and
  marks resolved, **not** a hard failure. Contrast and all *hard* checks still pass.
- **CLEAN (for a 100%-green scan):** no `background:` fills anywhere; color appears only in
  **navy `#061E3F` headings** and **borders** (gold/navy/blue/red/gray); body text default
  black, `<strong>` for emphasis; links default + underline. To convert RICH&rarr;CLEAN:
  delete every `background:` declaration, change white/`#cfdcec` band text to default (and
  the hero `<h2>` to navy), strip chromatic color from non-heading text. (Headings and
  borders are exempt from Ally's use-of-color check; **any** fill — even gray — is flagged.)

**The hard accessibility rules below apply to BOTH looks** and are non-negotiable.

## Hard accessibility rules (both looks — these are real Ally failures)
- **Real semantic headings.** One `<h2>` (the title) then `<h3>` sections — never fake a
  heading with bold/large `<span>`/`<p>` ("Headings may be missing" / "Styles instead of
  semantic markup"), and never skip a level (h2&rarr;h3, never h2&rarr;h4; "skipped headings").
  For sub-labels inside a card use bold `<div><strong>`, not a heading tag.
- **Descriptive image alt text — never the filename.** `alt="Console output: Casablanca runs
  90 minutes"`, not `alt="output.png"`. Decorative images &rarr; `alt=""`. **VIEW the image when
  you can** (see "Generating alt text" below) and describe what it shows; otherwise derive it
  from the surrounding text. Never leave a filename or a missing `alt` on an informative image.
- **Tables = real tabular data only, WITH headers.** A genuine data table keeps `<table>` and
  gets `<th scope="col">` (and `<th scope="row">` for labeled rows); Canvas preserves real
  tables. A table used only for layout &rarr; convert to label/value rows.
- **Every link has text.** No empty or icon-only `<a>` ("Link does not contain text").
- **Contrast >= 4.5:1.** All the hexes below are pre-checked: white/`#cfdcec`/gold on the navy
  bands and navy/slate on white all pass. The safe link blue on white is `#1565C0` (5.3:1).
- **Never meaning by color alone.** Alerts pair a red border **and** a ⚠ icon **and** a heading
  word; links carry `text-decoration: underline` (not color alone).
- **External / embedded content** ("Linked or embedded external content...") is about the
  *linked* resource (PDF, publisher site, embedded video), not your HTML — it **cannot** be
  auto-fixed; surface it to the instructor.

## Generating alt text (how to actually SEE Canvas images)
Course-hosted images can be downloaded and viewed, so alt text can be accurate, not guessed:
- An `<img src>` whose URL contains the **course's own id** and a **`?verifier=<hash>`** token
  is fetchable. Download and view it, then write alt from what you see:
  `curl -s -o tmp_img.png -L "<src url with &amp; turned back into &>"` then read the PNG.
- An image hosted in a **different** Canvas course (a publisher/master course id in the URL)
  usually returns **HTTP 403** to your token — you cannot view it; write alt from context.
- Decorative UI flourishes &rarr; `alt=""`. Keep every `src` URL exactly as-is.

## Palette
| Role | Hex |
|---|---|
| Navy — hero/footer fill, **heading text** (h2/h3), top/left borders | `#061E3F` |
| Pill fill (solid, on navy) | `#0E2C54` |
| Gold — top-bars, dividers, H3 underline, pill ring, eyebrow-on-navy | `#E9A821` |
| Blue — info/goal left-border | `#236192` · Subtitle-on-navy `#cfdcec` |
| Red — alert (border + heading) | `#C11F31` · alert fill `#fbe9eb` |
| Body text | `#2c3a4d` · Muted `#4b5563` · Card border `#d7dce3` · Light gray fill `#F5F5F5` |
| Info-box fill | `#eef4fa` · Link `#1565C0` (underlined) |

## Hard sanitizer rules (the Canvas RCE enforces these)
- **Inline `style="..."` only.** No `<style>`, no styling `class=`/`id=` (a Canvas file-link's
  own `class="instructure_file_link"` is functional — keep it). No `box-shadow`.
- **No `<br>`** (use `margin-top`), **no `<ol>`** (number inline), **no nested `<ul>`**, **no
  HTML comments**, **no web-font `<link>`**.
- Safe tags: `div, h2, h3, p, ul, li, a, strong, span, img` (+ a real data `<table>` with
  scope). Italics via `<span style="font-style: italic;">` (no `<em>`).
- Escape code: `<`&rarr;`&lt;`, `>`&rarr;`&gt;`, `&`&rarr;`&amp;`.

## Components (RICH form; the CLEAN swap is "drop the `background:` + de-color non-heading text")

**Wrapper:** `<div style="max-width: 980px; margin: 0 auto; font-family: Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55; color: #2c3a4d;"> ... </div>`

**Hero** (filled navy; eyebrow &rarr; h2 title &rarr; subtitle):
```html
<div style="padding: 24px; border-radius: 8px; background: #061E3F; border-top: 5px solid #E9A821;">
  <div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: #E9A821; font-weight: 700;">EYEBROW</div>
  <h2 style="margin: 6px 0 4px; font-size: 30px; font-family: Georgia, 'Times New Roman', serif; color: #ffffff;">TITLE</h2>
  <p style="margin: 0 0 14px; font-size: 15px; color: #cfdcec;">SUBTITLE</p>
</div>
```
**Pill** (solid, gold ring): `<span style="display: inline-block; background: #0E2C54; border: 1px solid #E9A821; border-radius: 999px; padding: 5px 14px; font-size: 13px; color: #ffffff; margin: 4px 6px 0 0;"><strong style="color: #E9A821;">LABEL</strong> value</span>`

**Card** (white, gold top-bar; navy `<h3>` with gold underline):
```html
<div style="margin-top: 18px; padding: 18px; border-radius: 8px; background: #ffffff; border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">
  <h3 style="margin: 0 0 12px; font-size: 19px; color: #061E3F;"><span style="border-bottom: 2px solid #E9A821; padding-bottom: 6px;">SECTION TITLE</span></h3>
  <p style="margin: 0; font-size: 14px; color: #2c3a4d;">Body.</p>
</div>
```
**List inside a card**: `<ul style="margin: 0; padding-left: 18px; font-size: 14px; color: #2c3a4d;"><li>...</li></ul>`

**Info / goal / tip box** (blue left-border):
```html
<div style="margin-top: 18px; padding: 10px 12px; border-radius: 8px; background: #eef4fa; border-left: 4px solid #236192; font-size: 13px; color: #2c3a4d;"><strong style="color: #061E3F;">&#127919; Lesson goal:</strong> ...</div>
```
**Alert** (red — border + icon + heading word, never color alone):
```html
<div style="margin-top: 18px; padding: 14px 16px; border-radius: 8px; background: #fbe9eb; border: 1px solid #f3c2c8; border-left: 5px solid #C11F31;">
  <div style="font-size: 14px; color: #C11F31; font-weight: 700; margin-bottom: 4px;">&#9888; Heading</div>
  <p style="margin: 0; font-size: 14px; color: #061E3F;">...</p>
</div>
```
**Code block** (preserves newlines, wraps on mobile):
```html
<div style="margin-top: 12px; padding: 12px 14px; border-radius: 8px; background: #F5F5F5; border: 1px solid #d7dce3; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; color: #2c3a4d; white-space: pre-wrap; overflow-x: auto;">REAL NEWLINES, &lt; &gt; &amp; ESCAPED</div>
```
**Inline code**: `<span style="font-family: 'Consolas', 'Courier New', monospace; background: #F5F5F5; padding: 1px 5px; border-radius: 4px; font-size: 13px; color: #061E3F;">code</span>`

**Real data table** (only for genuine tabular data; add scope):
```html
<table style="border-collapse: collapse; width: 100%; font-size: 14px;">
  <tr><th scope="col" style="border: 1px solid #d7dce3; padding: 6px; text-align: left;">Column</th> ...</tr>
  <tr><th scope="row" style="border: 1px solid #d7dce3; padding: 6px;">Row label</th><td style="border: 1px solid #d7dce3; padding: 6px;">cell</td></tr>
</table>
```
**Label/value row** (replaces layout tables): `<div style="margin-bottom: 6px;"><strong style="color: #061E3F;">Label:</strong> value</div>`
**Link**: `<a href="..." style="color: #1565C0; text-decoration: underline;">descriptive text</a>`

**Footer** (filled navy):
```html
<div style="margin-top: 18px; padding: 16px 18px; border-radius: 8px; background: #061E3F; border-top: 5px solid #E9A821; color: #ffffff;">
  <div style="font-size: 14px;">COURSE &middot; <strong style="color: #E9A821;">Course Name</strong> &middot; Week N</div>
  <div style="margin-top: 8px; font-size: 13px; color: #cfdcec;">One-line next step. Return to the course Page Index in Canvas.</div>
</div>
```

**Kept-emoji entities** (sparingly, always beside a text word): `&#127919;` 🎯 · `&#9989;` ✅ · `&#9888;` ⚠️.

## Page skeleton
hero &rarr; goal/intro info box &rarr; one card per source section (each with a real `<h3>`) &rarr;
(for assignments: Requirements, Deliverable, Grading rubric) &rarr; footer. Cover everything the
source covers; invent nothing. Exactly one `<h1>` = the Canvas page name (the hero title is `<h2>`).

## Syllabus pages — special handling
Render formal and plain (no decorative emoji). Convert hour-breakdown / grading-scale /
attendance tables to label/value rows (or real tables WITH scope). Put the full
nondiscrimination / Section 504 / ADA / Title IX statement in a light card. Emails as
underlined `mailto:` links. **Omit internal instructor notes** — surface them to the user.

## Mobile
Test at 360px; body text >= 14px; tap targets >= 44px; no fixed widths except the 980px
wrapper; grids collapse (`minmax(260px,1fr)`).

## Remediating an EXISTING course (gotchas)
- **Target the PUBLISHED page.** Canvas auto-appends `-2` to a duplicate-title slug, so a course
  can hold two same-titled pages (one published, one not). Restyle the one students see
  (`published: true`); flag the duplicate for the instructor to delete.
- Re-PUT **preserves real `<table>`s** (the "no layout table" rule is a house preference, not a
  Canvas-sanitizer limit). `class=` (styling), `box-shadow`, and `<style>` ARE stripped.
- Preserve existing instructional content **verbatim** when restyling; restructure + add semantic
  headings + fix accessibility only. Do not rewrite/humanize imported (e.g., publisher) content.
