# Per-page conversion spec

> **Scope:** this reference applies only to **Mode A (the optional Notion-import build
> path)**. For remediating or restyling an *existing* Canvas course, see
> `ada-remediation.md` (the dump → restyle → verify → push pipeline) instead.

This is the instruction block for converting ONE Notion page into one Canvas-safe
HTML file. Reuse it verbatim — give it to each subagent in the bulk workflow, or
follow it yourself when converting serially. It assumes the agent has the Notion
fetch tool and a Write tool.

Pair it with `style-guide.md` (the component library it refers to).

---

## Conversion instructions (give to the agent)

You convert ONE Notion page into a styled, Canvas-safe HTML file.

**STEP 1 — FETCH.** Load the Notion fetch tool and fetch the page by `id`. Use the
content returned, not the page's name from anywhere else.

**STEP 2 — CONVERT** following the style guide EXACTLY (inline styles only; no
tables/script/style/class/nested-lists/`<ol>`/`<br>`/box-shadow; one outer `<div>`,
no `<html>`/`<head>`). Map the Notion headings to `<h3>` section cards. Convert:
- 2-column tables → label/value rows.
- 3-column tables / outlines → bold heading + single-level `<ul>` (hierarchy).
- code fences → the code-block `<div>` (escape `< > &`, keep real newlines).
- assignment/project pages → include Requirements, Deliverable, and a Grading
  rubric card (label/value rows + a Total info box) **only if** the source has a
  points table.

**STEP 3 — FRAME.**
- Hero is an `<h2>` (Canvas owns the only `<h1>`); sections are `<h3>`.
- Eyebrow = `"<COURSE CODE> · Week N · <Kind>"` where Kind is Lesson (for a
  Read/Learn item), Assignment (a Do item), Project (a Build item), or Syllabus.
- Hero title = the **Notion page title** (keep its em dash if present).
- Footer references the course and this week.

**STEP 4 — HUMANIZE the prose** (body only): remove em/en dashes (commas/periods/
colons/parens), cut filler, hedging, AI-vocabulary, `-ing` padding, and
copula-avoidance (prefer is/are/has). Lighten mechanical bolding. Convert straight
quotes around phrases to `&ldquo;&rdquo;`. **Keep the em dash in the hero title.**
Keep emoji tasteful: `&#127919;` for a goal box, `&#9989;` for a Practice heading,
`&#9888;` for an alert; drop decorative 💡 (render tips as a plain "Tip:" info box).

**STEP 5 — SYLLABUS pages** are different: formal and plain, no decorative emoji;
all tables → label/value rows; full Section 504/ADA/Title IX statement in a
light-gray card; emails as underlined `#186FC8` mailto links; OMIT internal
instructor notes.

**STEP 6 — WRITE** the finished single-`<div>` HTML to the exact target path
(overwrite). Then report: `ok` plus any cross-course links you couldn't convert or
anything that looked off (e.g. the fetched content didn't match the expected
title — flag it, it matters for verification).

---

## Output schema (when used in a workflow)
Force a small structured return so the orchestrator can collect results:
```json
{ "type": "object", "additionalProperties": false,
  "properties": { "ok": {"type":"boolean"}, "notes": {"type":"string"} },
  "required": ["ok","notes"] }
```
The orchestrator builds the manifest from its own task list (title/module/slot),
not from agent returns — the agent only writes the file and flags anomalies.
