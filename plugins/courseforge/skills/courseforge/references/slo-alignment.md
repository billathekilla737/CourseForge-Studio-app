# SLO alignment (Mississippi state Student Learning Outcomes)

Use this to answer "**is my Canvas shell aligned to the SLOs for my program?**" Mississippi
community-college CTE programs are governed by a statewide **Mississippi Curriculum
Framework** per program, published by the **Mississippi Community College Board (MCCB)**.
Each framework defines, per course, a numbered list of **Student Learning Outcomes** with
lettered sub-outcomes. Those are the SLOs an instructor is audited against.

This file is the lookup table and the method. It does NOT contain the SLO text itself: the
frameworks are revised on their own cycle, so always pull the current PDF.

## Source of truth (re-check yearly)

**Top-level index of every program framework:**
**https://www.mccb.edu/curriculum**

That page is browsable by career cluster and, critically, **searchable by CIP Code and by
course Prefix**. Prefix search is the hook that makes this automatable: a Canvas course
named "IMT 1213 001" gives you the prefix `IMT`, which resolves to the owning program.

Individual program pages follow `https://www.mccb.edu/curriculum/<program-name-hyphenated>`
and carry a "Download Curriculum Framework PDF" link (current year) and often a "Download
Past Framework PDF" link (prior version).

## How to find the framework for any course

1. Read the Canvas course code (`GET /courses/:id` -> `course_code`), e.g. `IMT 1213 001`.
2. Take the **prefix** (`IMT`) and search the index by Prefix, or match a CIP code if the
   instructor knows it.
3. Open the program page, download the **current** framework PDF.
4. Extract the text **locally** (see below) and find the block for that course number.
5. Cross-check the SLOs against the course's assignments, quizzes and discussions.

## Extracting the PDF (do not use a web-to-markdown fetch)

These frameworks are text-based PDFs, but a web fetch-and-convert mangles them into
unreadable output. Extract locally instead:

```bash
python -c "import pymupdf; d=pymupdf.open('framework.pdf'); print('\n'.join(p.get_text() for p in d))"
```

`scripts/Remediate-CanvasPdfText.ps1 -Action Fetch` also scans PDF text if the framework has
been uploaded into a Canvas course.

## The structure inside a framework

Per course, in this order:

```
Course Number and Name:
IMT 1213 Game Theory and Mechanics

Description:
...

Hour Breakdown:
Semester Credit Hours / Lecture / Lab / Contact Hours

Prerequisite:
...

Student Learning Outcomes:
1.  <outcome>
    a.  <sub-outcome>
    b.  <sub-outcome>
2.  <outcome>
    a.  ...
```

The frameworks say **"Student Learning Outcomes"**. They do not use "Program Outcomes",
"Course Outcomes", or "Suggested Enabling Objectives" (a few say "Competencies" in passing).
There is **no separate program-level outcome list** in the 2025 Simulation framework:
alignment is judged course by course.

## The cross-check to run

For the course's SLOs, build a matrix of **sub-outcome -> the Canvas items that assess it**,
citing the specific assignment, quiz or discussion. Then report:

- **Unassessed SLOs** — a lettered sub-outcome no graded item covers. This is the finding
  that matters for an audit.
- **Weakly assessed SLOs** — covered only by a reading or an ungraded activity, never by
  anything scored.
- **Unaligned graded items** — an assignment that maps to no SLO. Not necessarily wrong
  (an instructor may add value beyond the floor), but worth surfacing.
- **Verb-level mismatch** — the SLO says "create", "implement", "compile", or "demonstrate",
  and the only evidence is an essay or a diagram. A framework that requires *producing* an
  artifact in software is not satisfied by writing about it.

Read the SLO verbs literally. "Evaluate 2D game engines", "Implement a sprite sheet
animation", and "Compile a game project for multiple target platforms" each demand a
different kind of evidence than analysis prose.

## Traps found in practice

- **Local course numbers drift from state numbers.** MGCCC teaches `IMT 2114` 3D Game Engine
  I, but the framework defines `IMT 2113` Game Engine 1. MGCCC teaches `IST 2824`
  Introduction to 3D Modeling, while the framework's equivalent is `IMT 1513` Introduction to
  3D Modeling. **Match on course TITLE as well as number**, and never conclude "not in the
  framework" from a number miss alone.
- **The 2025 Simulation framework contains both 3-hour and 4-hour variants** of some courses
  (`IMT 1213` and `IMT 1214` Game Theory; `IMT 2113` and a 4-hour engine course). Confirm
  which variant the college actually offers before judging hour breakdown.
- **One framework PDF can be linked under two CIP codes.** The 2025 Simulation framework is
  served both as `Simulation-Game-Design-Technology-2025.pdf` (CIP 50.0411) and
  `...-2025_0.pdf` (CIP 11.0804). Same document.
- **Colleges may run the prior-year framework through a transition semester.** If the course
  was built against the 2019 version, check that version too before reporting a gap.
- **Search-engine URLs for these PDFs go stale.** Paths under
  `/sites/mccb/files/Curriculum-PDFs/...` now 404; current files live under
  `/sites/default/files/<yyyy-mm>/...`. Always resolve the PDF from the program page rather
  than reusing a cached deep link.
- **Not every Canvas course maps to a CTE framework.** Academic-transfer courses (e.g. `CSC`)
  are governed by the statewide articulation/common-course numbering, not these CTE
  frameworks. Say so rather than forcing a match.

## Machine-readable program index

Programs confirmed relevant to MGCCC Simulation and Game Design. Extend as other programs
are needed; resolve anything absent through the index URL above.

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
      "framework_prior": "https://www.mccb.edu/sites/default/files/Divisions/Programs/Curriculum%20%26%20Instruction/Curriculum/Agriculutre-Food-Natural-Resources/Previous%20Versions/Simulation-Game-Design-Technology-2019.pdf",
      "prior_year": 2019,
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
      "name": "Extended Reality (XR) Courses/Certificates (Game Design, Augmented, Virtual and Mixed Reality)",
      "cip": ["11.0201", "11.0202"],
      "prefixes": ["IST"],
      "page": "https://www.mccb.edu/curriculum/extended-reality-xr-coursescertificates-game-design-augmented-virtual-and-mixed-reality",
      "framework_current": "https://www.mccb.edu/sites/default/files/mccb/files/Curriculum-PDFs/information-systems-technology/11.0201_11.0202_Extended-Reality-XR-Final-for-web.pdf",
      "framework_year": 2019
    }
  ]
}
```

## Courses defined in the 2025 Simulation framework

For fast prefix matching without re-downloading. Titles are abbreviated.

`IMT 1114` Introduction to Animation and Simulation Design · `IMT 1123` Vector Illustration ·
`IMT 1213` Game Theory and Mechanics · `IMT 1214` Game Theory (4 hr) · `IMT 1313` Video Game
Programming I · `IMT 1414` Graphic Editing for Games · `IMT 1513` Introduction to 3D Modeling ·
`IMT 1523` Intermediate 3D Modeling · `IMT 1613`/`IMT 1614` Advanced 3D Modeling ·
`IMT 2113` Game Engine 1 · `IMT 2143`/`IMT 2213` Business and Marketing for Game Design ·
`IMT 2223` Game Engine II · `IMT 2413` Animation & Simulation Design Capstone ·
`IMT 2513` Game Evaluation · `IMT 2613` Audio Design and Production ·
`IMT 2723` Introduction to XR Environment Production · `IMT 2733` Integrated 3D Production
Pipeline · `IMT 2743` Integrated XR Experience · `IMT 2753` Lighting and Shading ·
`IMT 2763` Introduction to XR Content Production · `IMT 2772` Simulation and Game Project ·
`IMT 2783`/`IMT 2738` Audio for Simulation and Games

## Institutional context

MGCCC's own program page (catalog-level description, not the SLO source):
https://mgccc.edu/programs/schools/engineering-mathematics-data-science-it/simulation-and-game-design-technology/
