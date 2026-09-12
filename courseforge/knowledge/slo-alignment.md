# SLO alignment (Mississippi Student Learning Outcomes) — any program

Answer "**is my Canvas shell aligned to the Student Learning Outcomes for my program?**" for
ANY instructor: automotive, networking, cyber security, cloud, welding, business, culinary,
health sciences, drafting, early childhood, and the rest.

Mississippi CTE programs are governed by a statewide **Mississippi Curriculum Framework** per
program, published by the **Mississippi Community College Board (MCCB)**. Each framework
defines, per course, a nested list of **Student Learning Outcomes**. Those are what an
instructor is audited against, and what this job cross-references.

## Source of truth (re-check yearly)

**Index of every program framework: https://www.mccb.edu/curriculum**

151 programs across 93 course prefixes, browsable by career cluster and searchable by **CIP
code** and by **course prefix**. Prefix search is what makes this automatable: a Canvas
`course_code` of `ATT 1214 001` yields `ATT`, which resolves to its program with no input from
the instructor. Program pages are `https://www.mccb.edu/curriculum/<program-name-hyphenated>`
and carry a current framework PDF plus, often, the prior version.

## The workflow

```
scripts\Check-SLOAlignment.ps1 -Action Resolve   # course_code -> candidate program(s)
scripts\Check-SLOAlignment.ps1 -Action Fetch     # framework PDF + SLOs + Canvas inventory
   ... the AGENT writes alignment.json (the judgment step) ...
scripts\Check-SLOAlignment.ps1 -Action Report    # validate, then render md + HTML
```

Same two-phase gateway as the PPTX/DOCX/PDF remediators: scripts collect facts and check the
result, the agent does the judging in between. `-Action Report` is **dry-run by default**;
`-Apply` pushes the report as an **unpublished** Canvas page.

`scripts/slo_framework_tool.py` does the mechanical work and can be driven directly:
`index` (cache the program list), `resolve`, `fetch`, `slos` (PDF -> outcomes),
`validate`, `report`.

## Writing alignment.json (the judgment step)

```json
{ "program": "...", "framework": "...pdf", "framework_year": 2025, "framework_url": "https://...",
  "alignment": [
    { "slo": "1.a",
      "verdict": "assessed | partially-assessed | ungraded-only | not-assessed | not-applicable",
      "evidence": [15977654, 5352405],
      "rationale": "why this verdict",
      "suggestion": "what to add or change (write one for every gap)" } ] }
```

`evidence` ids must come from `items.json`. **Read the SLO verb literally.** An outcome that
says *create*, *implement*, *compile*, *demonstrate*, or *produce* is not satisfied by an
essay about the topic. Distinguish these cases honestly:

- **assessed** — a graded item requires the thing the verb demands.
- **partially-assessed** — part of the outcome is met and part is not. Say which half.
- **ungraded-only** — a reading or ungraded activity covers it, nothing scored does.
- **not-assessed** — no item covers it.
- **not-applicable** — genuinely out of scope for this course (rare; justify it).

The validator **fails (exit 2)** if the mapping omits an outcome, invents an outcome id, cites
an item id that is not in the course, or claims coverage with no evidence. That gate is the
point: an alignment report is audit material, so it must not be able to hand-wave.

## What the report gives the instructor

A coverage percentage, an outcome-by-outcome table with evidence named, a **gaps section with
a concrete suggested fix per gap**, and a list of graded items that map to no outcome (not
necessarily wrong; an instructor may teach beyond the state floor). Markdown for program
review, Canvas-safe HTML for pushing into the course.

## Traps (each one hit in practice)

- **Prefix alone is often ambiguous.** `IST` maps to 10 programs, `BOT` to 8, `DDT` to 6.
  Resolve refuses to guess: match the course number AND title against each candidate
  framework's course list, or ask the instructor which program the course belongs to.
- **Local course numbers drift from state numbers.** MGCCC teaches `IMT 2114` 3D Game Engine I
  where the framework defines `IMT 2113`; MGCCC's `IST 2824` Introduction to 3D Modeling is the
  framework's `IMT 1513`. `slos --course X --title Y` falls back to number-only and then
  title-only matching and labels which kind of match it made. Never conclude "not in the
  framework" from a number miss alone.
- **A course can appear with NO outcomes.** Some courses show up only in a course-sequence
  table. `slos` refuses (exit 2) rather than letting an empty outcome list be reported as
  aligned.
- **Not every course has a CTE framework.** `ENG`, `MAT`, `CSC`, `NET`, `PNU`, `ACR` and other
  academic-transfer prefixes are absent from the index; they are governed by the statewide
  articulation agreement and common course numbering. `Fetch` exits 3 and says so. Report that
  plainly instead of forcing a match.
- **One framework PDF can serve several programs and CIP codes.** The Simulation 2025
  framework is served under both 50.0411 and 11.0804; the IT framework covers 11.0901,
  11.0201, 11.0802, 52.1302 and 11.1003 at once.
- **Frameworks differ in nesting and layout across years, and the parser handles all of it:**
  two levels (`1.` -> `a.`) in 2025, three (`1.` -> `a.` -> `(1)`) in 2017; the course code and
  title on one line, or the code alone with the title on the next (Welding 2018); an outcome
  number alone on its line with the text following; an optional "The student will" lead-in;
  and flat NATEF-style task lists of 40+ numbered items (Automotive). Verified against
  Simulation 2025, Automotive 2024, Welding 2018 and Information Systems 2017.
- **Search-engine deep links to framework PDFs go stale.** Paths under
  `/sites/mccb/files/Curriculum-PDFs/` now 404; current files live under
  `/sites/default/files/<yyyy-mm>/`. Always resolve from the program page.
- **A web-to-markdown fetch mangles these PDFs** into unusable text. Extract locally with
  PyMuPDF, which is what `slos` does.
- **Colleges may run the prior-year framework through a transition semester.** Use `-UsePrior`
  to check the older version before reporting a gap against a course built to it.

## Implementation notes (PowerShell traps found building this)

Worth knowing before editing `Check-SLOAlignment.ps1` or writing similar code:

- **`@(SomeFunction ...)` collapses an array return into ONE element.** Assigning first
  (`$x = SomeFunction ...`) preserves all N. Wrapping the call directly gave a single-element
  array holding the whole response, after which `$item.id` **silently member-enumerated** into
  an array of every id, and the inventory reported 2 items instead of 45. Assign, then iterate.
- **In `-like`, `?` is a single-character wildcard.** `$path -like '*?*'` is true for every
  non-empty string, so a query-separator test built that way appended `&` to every URL and
  produced 404s. Use `.Contains('?')`.
- **A stray `Write-Output` inside a function becomes part of its return value.** Diagnostics
  belong in `Write-Warning`/`Write-Verbose`, or they end up in the data.
- **A local variable whose name matches a parameter collides case-insensitively.** `$course`
  and a `[string]$Course` parameter are the same variable, so assigning the course object to it
  coerced it to a string and blanked every field.
- **`Out-File -Encoding utf8` writes a BOM in PS 5.1**, which Python's `utf-8` codec rejects.
  Write with `[IO.File]::WriteAllText($p, $json, (New-Object Text.UTF8Encoding($false)))`, and
  read tolerantly with `utf-8-sig`.
- **Git Bash rewrites a leading-slash argument into a Windows path**, so `--slug /curriculum/x`
  arrives as `C:/Program Files/Git/curriculum/x`. The tool normalizes any slug shape.

## Machine-readable program index

`slo_framework_tool.py index --cache <path>` caches the live index (name, CIP, prefixes,
slug). Prefer the cache for repeat runs and `--refresh` when it may be stale; the tool refuses
to proceed if it parses 0 programs, which is the signal that the site markup changed.

Verified programs relevant to MGCCC Simulation and Game Design:

```json
{
  "index": "https://www.mccb.edu/curriculum",
  "index_search": ["CIP Code", "Prefix"],
  "verified": "2026-08-12",
  "programs": [
    {
      "name": "Simulation and Game Design Technology",
      "listed_as": "Simulation and Animation Design",
      "cip": ["50.0411"],
      "prefixes": ["IMT", "WBL"],
      "page": "https://www.mccb.edu/curriculum/simulation-and-game-design-technology",
      "framework_current": "https://www.mccb.edu/sites/default/files/2026-02/Simulation-Game-Design-Technology-2025.pdf",
      "framework_year": 2025,
      "pages": 45
    },
    {
      "name": "Modeling, Virtual Environments and Simulation",
      "cip": ["11.0804"],
      "prefixes": ["IMT"],
      "page": "https://www.mccb.edu/curriculum/modeling-virtual-environments-and-simulation",
      "framework_current": "https://www.mccb.edu/sites/default/files/2026-02/Simulation-Game-Design-Technology-2025_0.pdf",
      "framework_year": 2025,
      "note": "Same PDF as CIP 50.0411, served under a second CIP code."
    },
    {
      "name": "Extended Reality (XR) Courses/Certificates",
      "cip": ["11.0201", "11.0202"],
      "prefixes": ["IST"],
      "page": "https://www.mccb.edu/curriculum/extended-reality-xr-coursescertificates-game-design-augmented-virtual-and-mixed-reality",
      "framework_year": 2019
    }
  ]
}
```

MGCCC's own program pages are catalog descriptions, not the SLO source. The framework PDF is
the authority.
