# CourseForge

Two tools for getting Canvas courses accessible, built for instructional
designers at Mississippi Gulf Coast Community College.

| | What it is | Who runs it | State |
|---|---|---|---|
| **CourseForge PDF Fixer** | A Windows desktop app that backs up, repairs and re-uploads a course's PDFs, and proves the result against PDF/UA-1 | An instructor, on their own PC, with no Python and no PowerShell | **v1.1.8 — deployable** |
| **CourseForge** | A Canvas remediation and course-building toolkit (PowerShell + Python) driven by the `courseforge` Claude Code skill | An instructional designer, with Claude Code | **Working toolkit, not yet a standalone product** |

They share one engine (`pdf_fastlane.py`) and one Canvas convention, which is
why they live in one repository.

---

## CourseForge PDF Fixer

A single-window app over seven verbs: connect a course, back up & fix, describe
images with Claude, upload, prove compliance, back up only, roll back.

**What it fixes, deterministically and fast** (~0.5 s/file, 220-file course in
about a minute):

- *Image-based file detected* → an OCR text layer (Tesseract word boxes drawn
  invisibly over untouched pixels)
- *File lacks tags* → a real, basic tag tree: `StructTreeRoot`, `MarkInfo`,
  per-block `P`/`H1` wired to marked-content IDs, figures tagged with alt
- Missing title / language → docinfo + XMP `dc:title`, `/Lang`,
  `DisplayDocTitle`
- Unembedded fonts, broken `ToUnicode` maps, `.notdef` references, table and
  heading-order defects in producer tag trees

**What it refuses to touch**: encrypted, digitally signed, or already-tagged
files it cannot audit. Those go to `queue.json` for a person, never damaged.

**Honest scope.** The tags are real and standards-valid but *basic* —
paragraphs, simple headings, figures, in content-stream order. That satisfies
automated checkers and gives assistive-technology users a navigable document.
It is not full PDF/UA semantic fidelity. The app says so, and it reports the
gap rather than hiding it:

- figures it could not produce a picture of (so no one can describe them) are
  counted and **named**
- alt text that is only a filename (`image0.jpeg`) or `Picture 3` is reported
  and **deliberately left alone** — it is the author's content, not ours to
  overwrite
- a green PDF/UA-1 report alongside either of those means
  "standards-compliant, not yet genuinely accessible", and the app prints both
  facts

Every output is independently verified — text preserved, render effectively
identical, tag tree present — or it is deleted and queued. Originals are always
kept locally, and `ROLL BACK` restores them.

Install and first-use instructions: [`installer/INSTALL-GUIDE.md`](installer/INSTALL-GUIDE.md).

### Building it

Source lives in `skill/scripts/`. The build is driven from `installer/`:

```powershell
python -m PyInstaller courseforge-pdf.spec --distpath dist --workpath build
# then place tesseract\, verapdf\ and jre\ beside dist\courseforge-pdf\courseforge-pdf.exe
# and compile courseforge-pdf.iss with Inno Setup
```

The spec locates the source via `CF_SCRIPTS`, then the installed skill, then
`..\skill\scripts`. Build output is **not** committed — the installer is
~141 MB, past GitHub's 100 MB file limit. Ship it as a GitHub Release.

Before shipping a build:

```powershell
python skill\scripts\pdf_fastlane.py selftest      # must print SELFTEST PASS
```

---

## CourseForge (the toolkit)

`skill/` is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code):
`SKILL.md` is the playbook, `references/` the detail, `scripts/` the verbs. A
designer describes the job; Claude Code drives these scripts.

The remediation pipeline for an existing course is three steps, and the middle
one is the point:

```powershell
.\Dump-CanvasContent.ps1  -CourseId 123456 -WorkDir .\work   # pull bodies
python restyle_html.py transform .\work --look clean         # restyle
python restyle_html.py verify    .\work                      # PROVE text unchanged
.\Push-CanvasRemediation.ps1 -WorkDir .\work                 # dry run
.\Push-CanvasRemediation.ps1 -WorkDir .\work -Apply          # write
```

`restyle_html.py` can only alter `style=""` attributes and add wrapper markup —
it physically cannot rewrite instructional prose — and `verify` proves it by
comparing the visible text, link set and image set of every output against its
original. `Push-CanvasRemediation.ps1` refuses to run unless that report passed
*and still matches the files on disk*.

Other jobs the toolkit covers: PowerPoint and Word remediation, course export /
import / clone, due-date computation from the term calendar, SLO alignment
checking, blind-grading bundles, and left-nav trimming. See `skill/SKILL.md`.

### Conventions

- **Credentials.** Canvas tokens are stored DPAPI-encrypted
  (`canvas.token.enc`), readable only by the Windows account that saved them on
  the machine that saved them. The PDF Fixer keeps its copy in
  `%LOCALAPPDATA%`, never in a cloud-synced Documents folder. A legacy
  plaintext `canvas.token` is migrated and deleted automatically.
- **Dry-run first.** Every script that writes to Canvas takes `-Apply` and does
  nothing without it.
- **Course scope.** A script never resolves a course silently: ambiguity is a
  hard error, and a config picked from a fallback folder is announced.
- **Never student data.** `.gitignore` excludes grade bundles, rosters and
  working caches. Nothing in this repo contains student information.
- **Branding.** `skill/brand.json` holds the palette and fonts for generated
  markup. The colour literals in `restyle_html.classify()` are a *detector* for
  existing MGCCC template markup, not a style choice — see `DETECTOR_NOTE`.

### Tests

```powershell
python skill\scripts\pdf_fastlane.py selftest       # PDF engine + alt pipeline
python skill\scripts\test_restyle_html.py           # the restyler's verify gate
```

Both are dependency-free and must pass before any change ships.

---

## Repository layout

```
skill/          the courseforge Claude Code skill - THE SOURCE
  SKILL.md        playbook
  brand.json      palette + fonts for generated markup
  references/     detailed guides (ADA remediation, style, calendar, SLO...)
  scripts/        22 PowerShell verbs + 12 Python tools
installer/      PyInstaller spec, Inno Setup script, icon, install guide
sync-skill.ps1  keep skill/ and the live ~\.claude\skills\courseforge in step
```

While you work, the live skill at `%USERPROFILE%\.claude\skills\courseforge\`
is what Claude Code loads. `skill/` is the committed copy. Move between them
with `sync-skill.ps1` (run it with no arguments to see what differs) — do not
edit both by hand.

Working directories (`courses/`, `audit/`, `pdf-lab/`, `canvas-export/`) hold
real pulled course content and are intentionally untracked.

## Licence

CourseForge's own source is MIT — see [`LICENSE`](LICENSE).

The **installer** is a different matter: it redistributes veraPDF (GPLv3/MPLv2),
Tesseract (Apache-2.0) and an Eclipse Temurin JRE (GPLv2+CPE), and the frozen
executable embeds PyMuPDF, which is **AGPL-3.0 or commercial**. Read
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) before publishing installer
binaries — it lists what is bundled, what each licence requires, and the one
open question (PyMuPDF) that needs a decision first. Publishing this source
repo is unaffected.
