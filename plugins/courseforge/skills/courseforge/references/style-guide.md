# Canvas-safe HTML style guide (ADA / Ally-clean "bordered" template)

Inline-styled components that survive the Canvas Rich Content Editor sanitizer **and**
pass an Anthology Ally accessibility scan at 100%. Colors below are the MGCCC
"Mississippi" blue-and-gold palette — swap the hexes for another school, but keep the
*structure* (border-only, no fills) — that's what keeps Ally happy.

## The accessibility rule that shapes everything (WCAG 1.4.1 — "use of color")
MGCCC scans every page/assignment/quiz with **Anthology Ally**. Ally's
*"Potential use of color alone to communicate information"* flag fires on exactly two things,
so the whole design avoids both:

1. **No `background:` color fills.** ANY background fill is flagged — even a neutral gray.
   Convey structure with **borders, not fills** (top-bars, left-accents, rings, underlines).
2. **No chromatic *body* text.** Colored `<strong>`/`<span>`/`<p>` runs are flagged.
   Body text is **default black**; emphasis is **`<strong>` bold**, never color.

What Ally does **not** flag (so we use these freely for brand):
- **Navy `#061E3F` on `<h2>`/`<h3>` headings** (headings are exempt at any count).
- **Borders of any color** — gold, navy, blue, gray, red.

So **color appears in only two places: heading text (navy) and borders (gold/navy/blue/red/gray).**
This passed Ally at **100%** on a full course. (Contrast — WCAG 1.4.3 — is automatically
satisfied: default-black and navy text on white are >11:1.)

## Hard rules (the Canvas sanitizer enforces these)
- **Inline `style="..."` only.** No `<style>`, no `class=`/`id=` styling.
- **No `<table>`.** Use label/value rows or a bold heading + single-level `<ul>`.
- **No nested lists** (`<ul>` in `<li>`), **no `<ol>`** (put the number in the text),
  **no `<br>`** (use `margin-top`), **no HTML comments**, **no `box-shadow`**.
- **No web-font `<link>`** — always include web-safe fallbacks in `font-family`.
- Safe tags: `div, h2, h3, p, ul, li, a, strong, span, img`. Italics via
  `<span style="font-style: italic;">` (no `<em>`).
- Escape code: `<`→`&lt;`, `>`→`&gt;`, `&`→`&amp;`. Entities for punctuation:
  `&mdash; &amp; &rarr; &middot; &ldquo; &rdquo;`.

## Palette (color lives only in headings + borders)
| Role | Hex |
|---|---|
| Navy — **heading text** (h2/h3) **only**, and top/left borders | `#061E3F` |
| Gold — top-bars, dividers, H3 underline, pill rings (border only) | `#E9A821` |
| Blue — info/goal **left-border** | `#236192` |
| Red — alert **left-border** | `#C11F31` |
| Card border / code border / neutral structure | `#d7dce3` |
| **Body text** | default (no `color:`) — emphasis via `<strong>` |
| **Links** | default color + `text-decoration: underline` |

**Never** set `color:` on body text, and **never** set a `background:` color. If you ever
must color non-heading text, the only AA-safe link blue is `#1565C0` (5.3:1 on white) — but
prefer default + underline.

## Components (copy the inline styles — note: no `background` anywhere)

**Wrapper** (one outer div; no body color):
```html
<div style="max-width: 980px; margin: 0 auto; font-family: Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55;"> ... </div>
```

**Header block** (gold top-bar, navy `<h2>` title, default eyebrow/subtitle — NO fill):
```html
<div style="padding: 24px; border-radius: 8px; border-top: 5px solid #E9A821;">
  <div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; font-weight: 700;">EYEBROW</div>
  <h2 style="margin: 6px 0 4px; font-size: 30px; font-family: Georgia, 'Times New Roman', serif; color: #061E3F;">TITLE</h2>
  <p style="margin: 0 0 14px; font-size: 15px;">SUBTITLE</p>
</div>
```
**Pill / chip** (gold ring, no fill, default text):
`<span style="display: inline-block; border: 1px solid #E9A821; border-radius: 999px; padding: 5px 14px; font-size: 13px; margin: 4px 6px 0 0;"><strong>LABEL</strong> value</span>`

**Card** (gray border + gold top-bar, no fill; navy `<h3>` with gold underline):
```html
<div style="margin-top: 18px; padding: 18px; border-radius: 8px; border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">
  <h3 style="margin: 0 0 12px; font-size: 19px; color: #061E3F;"><span style="border-bottom: 2px solid #E9A821; padding-bottom: 6px;">SECTION TITLE</span></h3>
  <p style="margin: 0; font-size: 14px;">Body.</p>
</div>
```
**List inside a card**: `<ul style="margin: 0; padding-left: 18px; font-size: 14px;"><li>...</li></ul>`

**Info / goal / tip box** (blue left-border, no fill):
```html
<div style="margin-top: 18px; padding: 10px 12px; border-radius: 8px; border-left: 4px solid #236192; font-size: 13px;"><strong>&#127919; Lesson goal:</strong> ...</div>
```

**Alert** (red left-border + icon + heading WORD, no fill — meaning never by color alone):
```html
<div style="margin-top: 18px; padding: 14px 16px; border-radius: 8px; border: 1px solid #d7dce3; border-left: 5px solid #C11F31;">
  <div style="font-size: 14px; font-weight: 700; margin-bottom: 4px;">&#9888; Heading</div>
  <p style="margin: 0; font-size: 14px;">...</p>
</div>
```

**Code block** (gray border, no fill; preserves newlines, wraps on mobile):
```html
<div style="margin-top: 12px; padding: 12px 14px; border-radius: 8px; border: 1px solid #d7dce3; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; white-space: pre-wrap; overflow-x: auto;">REAL NEWLINES, &lt; &gt; &amp; ESCAPED</div>
```
**Inline code**: `<span style="font-family: 'Consolas', 'Courier New', monospace; border: 1px solid #d7dce3; padding: 1px 5px; border-radius: 4px; font-size: 13px;">code</span>`

**Label/value row** (replaces 2-column tables): `<div style="margin-bottom: 6px;"><strong>Label:</strong> value</div>`
**Hierarchy** (replaces 3-column tables / outlines): a `<div><strong>Heading</strong></div>` then one `<ul>` of sub-points.
**Grading rubric**: a card of label/value rows like `<div style="margin-bottom: 6px;">Criterion <strong>(20 pts)</strong></div>`, then a total row `<strong>Total: 100 points</strong>`.
**Link**: `<a href="..." style="text-decoration: underline;">text</a>` (default color; underline is the non-color cue).

**Footer** (gold top-bar, no fill, default text):
```html
<div style="margin-top: 18px; padding: 16px 18px; border-radius: 8px; border-top: 5px solid #E9A821;">
  <div style="font-size: 14px;">COURSE &middot; <strong>Course Name</strong> &middot; Week N</div>
  <div style="margin-top: 8px; font-size: 13px;">One-line next step. Return to the course Page Index in Canvas.</div>
</div>
```

**Kept-emoji entities** (use sparingly, always beside a text word so meaning isn't icon-only):
`&#127919;` 🎯 goal · `&#9989;` ✅ practice · `&#9888;` ⚠️ alert.

## Accessibility checklist
- Exactly one `<h1>` = the Canvas page name. Header-block title is `<h2>`, sections `<h3>`.
- **Color only in headings (navy) + borders.** No `background:` fills, no chromatic body text.
- Every `<img>` has `alt` (decorative → `alt=""`; never the filename).
- Don't encode meaning in color alone (alerts use a red border **and** ⚠ icon **and** a heading word).
- Links carry `text-decoration: underline` (the non-color cue).
- Mobile: test at 360px, body text ≥ 14px, tap targets ≥ 44px, no fixed widths except the
  980px wrapper, grids collapse (`minmax(260px,1fr)`).

## Page skeleton
header block → goal/intro info box → one card per source section → (for assignments:
Requirements, Deliverable, Grading rubric) → footer. Cover everything the source covers;
invent nothing.

## Syllabus pages — special handling
Render formal and plain (no decorative emoji). Convert every table (hour breakdown, grading
scale, attendance) to label/value rows. Put the full nondiscrimination / Section 504 / ADA /
Title IX statement in a gray-bordered card (no fill). Make emails `mailto:` links, underlined.
**Omit internal instructor notes** (e.g. "verify course number/credits") — surface them to the
user instead.
