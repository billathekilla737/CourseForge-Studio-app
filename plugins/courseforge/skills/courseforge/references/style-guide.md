# Canvas-safe HTML style guide

Inline-styled components that survive the Canvas Rich Content Editor sanitizer.
Colors/typography below are the MGCCC "Mississippi" palette — swap the hexes for
another school, but keep the *structure* (it's what survives the sanitizer).

## Hard rules (the sanitizer enforces these)
- **Inline `style="..."` only.** No `<style>`, no `class=`/`id=` styling.
- **No `<table>`.** Use label/value rows or a bold heading + single-level `<ul>`.
- **No nested lists** (`<ul>` inside `<li>`), **no `<ol>`** (put the number in the
  text), **no `<br>`** (use `margin-top` for spacing), **no HTML comments**.
- **No `box-shadow`** (this instance strips it — use borders for definition).
- **No web-font `<link>`** — always include web-safe fallbacks in `font-family`.
- Safe tags: `div, h2, h3, p, ul, li, a, strong, span, img`. Italics via
  `<span style="font-style: italic;">` (no `<em>`).
- Escape code: `<`→`&lt;`, `>`→`&gt;`, `&`→`&amp;`. Use entities for punctuation:
  `&mdash; &amp; &rarr; &middot; &ldquo; &rdquo;`.

## Palette
| Role | Hex |
|---|---|
| Navy (hero/footer/headings/strong) | `#061E3F` |
| Blue (info-box left border) | `#236192` |
| Link | `#186FC8` |
| Gold (top borders, dividers, H3 underline, pills on navy) | `#E9A821` |
| Body text | `#2c3a4d` · Muted `#4b5563` · Subtitle-on-navy `#cfdcec` |
| Info box bg | `#eef4fa` · Card border `#d7dce3` · Light gray `#F5F5F5` |
| Alert: red `#C11F31` · bg `#fbe9eb` · border `#f3c2c8` |

**Gold is decorative only** (fails AA as text on light) — borders/dividers/pills-on-navy.

## Accessibility
- Exactly one `<h1>` = the Canvas page name. Hero title is `<h2>`, sections `<h3>`.
- Every `<img>` has `alt` (decorative → `alt=""`; never the filename).
- Don't encode meaning in color alone (alerts use red **and** a text heading).
- Mobile: test at 360px, body text ≥ 14px, tap targets ≥ 44px, no fixed widths
  except the 980px wrapper, grids collapse (`minmax(260px,1fr)`).

## Components (copy the inline styles)

**Wrapper** (one outer div):
```html
<div style="max-width: 980px; margin: 0 auto; font-family: Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55; color: #2c3a4d;"> ... </div>
```

**Hero** (navy; eyebrow → h2 title → subtitle → optional pills):
```html
<div style="padding: 24px; border-radius: 8px; background: #061E3F; border-top: 5px solid #E9A821;">
  <div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: #E9A821; font-weight: 700;">EYEBROW</div>
  <h2 style="margin: 6px 0 4px; font-size: 30px; font-family: Georgia, 'Times New Roman', serif; color: #ffffff;">TITLE</h2>
  <p style="margin: 0 0 14px; font-size: 15px; color: #cfdcec;">SUBTITLE</p>
</div>
```
**Pill** (on navy): `<span style="display: inline-block; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.28); border-radius: 999px; padding: 5px 14px; font-size: 13px; color: #ffffff; margin: 4px 6px 0 0;"><strong style="color: #E9A821;">LABEL</strong> value</span>`

**Card** (white, gold top-border):
```html
<div style="margin-top: 18px; padding: 18px; border-radius: 8px; background: #ffffff; border: 1px solid #d7dce3; border-top: 4px solid #E9A821;">
  <h3 style="margin: 0 0 12px; font-size: 19px; color: #061E3F;"><span style="border-bottom: 2px solid #E9A821; padding-bottom: 6px;">SECTION TITLE</span></h3>
  <p style="margin: 0; font-size: 14px; color: #2c3a4d;">Body.</p>
</div>
```
**List inside a card**: `<ul style="margin: 0; padding-left: 18px; font-size: 14px; color: #2c3a4d;"><li>...</li></ul>`

**Info / goal / tip box** (blue left border):
```html
<div style="margin-top: 18px; padding: 10px 12px; border-radius: 8px; background: #eef4fa; border-left: 4px solid #236192; font-size: 13px; color: #2c3a4d;"><strong style="color: #061E3F;">&#127919; Lesson goal:</strong> ...</div>
```

**Alert** (red; warnings/must-read):
```html
<div style="margin-top: 18px; padding: 14px 16px; border-radius: 8px; background: #fbe9eb; border: 1px solid #f3c2c8; border-left: 5px solid #C11F31;">
  <div style="font-size: 14px; color: #C11F31; font-weight: 700; margin-bottom: 4px;">&#9888; Heading</div>
  <p style="margin: 0; font-size: 14px; color: #061E3F;">...</p>
</div>
```

**Code block** (replaces `<pre>`; preserves newlines, wraps on mobile):
```html
<div style="margin-top: 12px; padding: 12px 14px; border-radius: 8px; background: #F5F5F5; border: 1px solid #d7dce3; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; color: #2c3a4d; white-space: pre-wrap; overflow-x: auto;">REAL NEWLINES, &lt; &gt; &amp; ESCAPED</div>
```
**Inline code**: `<span style="font-family: 'Consolas', 'Courier New', monospace; background: #F5F5F5; padding: 1px 5px; border-radius: 4px; font-size: 13px; color: #061E3F;">code</span>`

**Label/value row** (replaces 2-column tables):
```html
<div style="margin-bottom: 6px;"><strong style="color: #061E3F;">Label:</strong> value</div>
```
**Hierarchy** (replaces 3-column tables / outlines): a `<div><strong>Heading</strong></div>` then one `<ul>` of sub-points.

**Grading rubric** (assignment/project pages): a card of label/value rows like
`<div style="margin-bottom: 6px;">Criterion <strong style="color: #061E3F;">(20 pts)</strong></div>`,
then a total info box `<strong>Total: 100 points</strong>`.

**Footer** (navy):
```html
<div style="margin-top: 18px; padding: 16px 18px; border-radius: 8px; background: #061E3F; border-top: 5px solid #E9A821; color: #ffffff;">
  <div style="font-size: 14px;">COURSE &middot; <strong style="color: #E9A821;">Course Name</strong> &middot; Week N</div>
  <div style="margin-top: 8px; font-size: 13px; color: #cfdcec;">One-line next step. Return to the course Page Index in Canvas.</div>
</div>
```

**Kept-emoji entities** (use sparingly): `&#127919;` 🎯 goal · `&#9989;` ✅ practice · `&#9888;` ⚠️ alert.

## Page skeleton
hero → goal/intro info box → one card per source section → (for assignments:
Requirements, Deliverable, Grading rubric) → footer. Cover everything the source
covers; invent nothing.

## Syllabus pages — special handling
Render formal and plain (no decorative emoji). Convert every table (hour
breakdown, grading scale, attendance) to label/value rows. Put the full
nondiscrimination / Section 504 / ADA / Title IX statement in a light-gray
(`#F5F5F5`) card. Make emails `mailto:` links, underlined, `#186FC8`. **Omit any
internal instructor notes** (e.g. "verify course number/credits") — those are
authoring asides, not student content; surface them to the user instead.
