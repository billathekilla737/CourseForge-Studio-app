# CourseForge Studio

One local web app for Canvas instructors. It grades, it makes courses
accessible, it builds content, and it runs the chores around a course, all from
one page on your own computer, using the Claude Code login you already have.

It grew out of two tools by the same author: **canvas-grader** (the review UI
and the safety model) and **CourseForge** (the ADA remediation toolkit, the
PDF/UA engine, the content builder, the Assistant). Both are superseded by this
repository. See [docs/MIGRATION.md](docs/MIGRATION.md) for what moved where.

---

## What it does

Open a course and you get five areas. Use any of them alone.

| Area | What it is for |
|---|---|
| **Grade** | Pulls an assignment's rubric and submissions, drafts a rubric-aligned grade and a two-sentence comment for every student with Claude, and gives you a side-by-side review before a single point reaches Canvas. Insights, curves, overlap check, a teaching read, quiz settings and accommodations, the term schedule. The manual is [docs/GRADING.md](docs/GRADING.md). |
| **Accessibility** | Brings an existing course up to ADA / Ally: restyles every page, assignment, discussion, quiz description and the syllabus with a gate that proves the visible text did not change, then pushes in place. Fixes PowerPoint and Word files (alt text, slide titles, header rows). Fixes PDFs with a PDF/UA-1 engine (OCR layer, tag tree, title and language, fonts) validated with veraPDF, describes figures with Claude, and uploads over the originals so links keep working. Edits text inside PDFs and Office files (a former instructor's name in every handout). |
| **Build** | Drafts a page, syllabus, assignment, graded discussion, quiz or study guide in the school's Canvas-safe template, checks it against the style rules, and places it in the module you choose, unpublished by default. Project manifests for whole modules. Rubrics. |
| **Tools** | Export a course to `.imscc`, import a cartridge, copy a course into a sandbox. Roll due dates to a new term from the academic calendar with a week-by-week table to approve. Trim the left navigation. Back up a quiz before rewriting it. Cross-reference the course against the state learning outcomes for its program. |
| **Assistant** | A Claude Code session for the course, in the browser. Say what you want in plain English. It drives the same verbs as the buttons, and every time it wants to change Canvas a card appears asking you to Allow or Deny. No answer means no. |

Three rules hold everywhere:

- **Nothing reaches Canvas on one click.** Every write is refused once by the
  server, shown to you as a plain sentence, and sent only when you confirm that
  exact change. Dry run first, always.
- **The tool proves before it pushes.** Restyled HTML must pass a visible-text
  comparison; fixed PDFs must survive an independent text and render check or
  they are deleted and queued for a person; documents that fail verification
  are never uploaded.
- **Student data stays on this machine.** Grading pseudonymises students before
  anything is sent to Claude, and the identity map never leaves your computer.
  Every other area works through a Canvas client that cannot reach submissions,
  grades or rosters at all.

---

## Requirements

- **Python 3.10+** (3.12 recommended)
- **Claude Code CLI**, installed and signed in: <https://claude.com/claude-code>
- A **Canvas API token** for your own account (the setup screen walks you through it)
- Optional, for the PDF fixer's OCR and compliance proof: **Tesseract**, **veraPDF** and a **Java** runtime.
  The first launch offers to install the two that have official packages, and
  tells you the two steps for veraPDF. You can decline: everything else works
  without them, and the accessibility area says what is missing where it matters.
  Later, `python -m courseforge tools` reports the same thing and
  `python -m courseforge tools --install --yes` does the install.

## Setup

```bash
git clone https://github.com/billathekilla737/CourseForge-Studio
cd CourseForge-Studio
pip install -e .[pdf]
```

Copy the example config (only `base_url` needs changing if you are not at MGCCC):

```bash
cp config.example.json config.json
```

On Windows PowerShell: `Copy-Item config.example.json config.json`

Your Canvas token does **not** go in that file. Start the app and it will show a
**Connect to Canvas** button; paste the token there. It is checked against
Canvas, then stored encrypted under your user profile
(`%APPDATA%\CourseForge-Studio\canvas.token.enc`, readable only by your Windows
account on this machine). A token saved by the old canvas-grader or CourseForge
tools is found and migrated the first time you save.

Check everything:

```bash
python -m courseforge doctor
```

## Running it

**Windows, no terminal:** double-click **`CourseForge Studio.vbs`**. A small
window says it is running, with a button that opens the app in your browser.
Closing that window stops the server and any Claude work in flight.

**From a terminal:**

```bash
python -m courseforge serve      # http://127.0.0.1:8900
```

Run it from a normal terminal, not from inside a Claude Code session, so the
Claude CLI can see your login.

## From the command line

Every area's verbs are also commands, which is what the Assistant runs:

```bash
python -m courseforge a11y dump --course 734391
python -m courseforge a11y restyle --course 734391 --look clean
python -m courseforge a11y verify --course 734391
python -m courseforge a11y push --course 734391            # dry run
python -m courseforge a11y push --course 734391 --apply    # asks for a typed yes
python -m courseforge pdf fix --course 734391
python -m courseforge course due-dates --course 734391
python -m courseforge --help
```

A verb that writes to Canvas does nothing without `--apply`, and prints the plan either way.

---

## Configuration

`config.json` keys are documented inline in
[config.example.json](config.example.json). The ones people change:

| Key | Default | Notes |
|---|---|---|
| `base_url` | `https://mgccc.instructure.com` | Your Canvas host |
| `model` | `opus` | Grading model (`opus`, `sonnet`, `haiku`) |
| `describe_model` | `sonnet` | Alt text and image descriptions |
| `a11y_look` | `clean` | Restyle look; `clean` scores zero advisory flags in Ally |
| `pdf_jobs` | `0` | PDF engine worker processes; 0 = CPU count minus 2 |
| `assistant_ask_timeout_s` | `1200` | An unanswered Allow/Deny is a Deny after this long |
| `allow_canvas_writes` | `true` | Hard read-only lock when `false` |
| `llm_backend` | `cli` | `api` switches to an API key (hosted build; see below) |

`CANVAS_TOKEN`, `CANVAS_BASE_URL` and `CANVAS_GRADER_PORT` override the file.

---

## Layout

```
CourseForge Studio.vbs   double-click launcher (hidden console)
run.cmd                  terminal launcher
courseforge/             the app
  server.py              local HTTP API, background jobs, the confirm gate
  routing.py, areas.py   how the areas plug in
  canvas*.py             Canvas client; canvas_policy.py decides what each area may touch
  llm.py                 one seam for every model call (claude_cli.py locally, claude_api.py hosted)
  secrets.py             encrypted token storage
  a11y/ docs/ pdf/ content/ courseops/ assistant/   the areas
  hub/                   the course hub
  knowledge/             the remediation, style and Canvas-API knowledge the tool and the Assistant follow
  web/                   the UI (vanilla JS, no build step)
skill/                   the Claude Code skill the Assistant loads
docs/                    manuals, contracts, the school deployment plan
legacy/                  the PowerShell verbs and desktop apps this replaced
tests/                   python -m unittest discover tests
data/                    everything downloaded or produced (gitignored)
```

## Hosting it for a school

The local build uses your Claude Code login and stores work on your machine. A
school-hosted build swaps in an API key, Postgres, single sign-on and a worker
queue behind the same code. The design, the data model, the security controls
and a phased rollout are in [docs/DEPLOYMENT-SCHOOL.md](docs/DEPLOYMENT-SCHOOL.md).

## Documentation

- [docs/GRADING.md](docs/GRADING.md): the grading manual
- [docs/UI-DESIGN.md](docs/UI-DESIGN.md): how the UI is organised
- [docs/AREA-CONTRACT.md](docs/AREA-CONTRACT.md) and [docs/FRONTEND-CONTRACT.md](docs/FRONTEND-CONTRACT.md): how to add an area
- [docs/MIGRATION.md](docs/MIGRATION.md): from canvas-grader and CourseForge
- [docs/DEPLOYMENT-SCHOOL.md](docs/DEPLOYMENT-SCHOOL.md): the hosted build
- [docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md): licences of the PDF tools (PyMuPDF is AGPL; read before redistributing)
- `courseforge/knowledge/`: the ADA remediation playbook, the style guide, the Canvas API silent-failure catalogue, the academic calendar, SLO alignment

## Licence

MIT for this repository's own source. See [LICENSE](LICENSE) and the third-party notices.

Grades produced by this tool are drafts, and course changes are yours to
confirm. You are the instructor of record.
