# CourseForge

Two tools for getting Canvas courses accessible, built for instructional
designers at Mississippi Gulf Coast Community College.

| | What it is | Who runs it | State |
|---|---|---|---|
| **CourseForge PDF Fixer** | A Windows desktop app that backs up, repairs and re-uploads a course's PDFs, and proves the result against PDF/UA-1 | An instructor, on their own PC, with no Python and no PowerShell | **v1.1.8 — deployable** |
| **CourseForge Assistant** | A Windows desktop app: one prompt box over the whole toolkit, with Claude Code working behind the window and an Allow / Deny dialog for every change to Canvas | An instructor or instructional designer, on their own PC, with Claude Code signed in | **v0.1.0 — first build** |
| **CourseForge** | A Canvas remediation and course-building toolkit (PowerShell + Python) driven by the `courseforge` Claude Code skill | An instructional designer, with Claude Code — or anyone, through the Assistant | **Working toolkit** |

The two apps share one window shell (`cf_theme.py`), the PDF Fixer and the
toolkit share one engine (`pdf_fastlane.py`), and all three share one Canvas
convention, which is why they live in one repository.

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
python -m pip install -r installer\requirements-build.txt
.\Build-PdfFixer.ps1 -ToolsFrom <folder holding tesseract\ verapdf\ jre\>   # -SignThumbprint <sha1> for a signed release
```

The spec locates the source via `CF_SCRIPTS`, then the installed skill, then
`..\skill\scripts`. Build output is **not** committed — the installer is
~141 MB, past GitHub's 100 MB file limit. Ship it as a GitHub Release.

Before shipping a build:

```powershell
python skill\scripts\pdf_fastlane.py selftest      # must print SELFTEST PASS
```

---

## CourseForge Assistant

The toolkit below, without the terminal. It wears the same window shell as the
PDF Fixer (shared through `skill/scripts/cf_theme.py`): course picker, six
quick-job suggestions, a plain-language prompt box, a conversation pane. Behind
it, Claude Code runs headless with the `courseforge` skill in one long-lived
streaming session per course, so follow-ups continue where they left off and
Claude reads its own results and corrects course.

**The safety gate.** `cf_assistant_hook.py` is wired in as a Claude Code
`PreToolUse` hook on every tool call. Reads, dumps, transforms and dry runs go
through on their own. Anything that writes to Canvas (`-Apply`, the three
scripts that write on sight, a raw PUT/POST/DELETE) or changes the PC makes the
hook phone the window over localhost and block until the person clicks
**Allow** or **Deny**. No window to ask means deny: it fails closed, and the
hook never denies on its own judgement — every deny is a person's click. The
rules are pure functions with tests (`test_courseforge_assistant.py`).

**What it needs on the PC.** Claude Code, signed in with the employee's own
Claude account (the app detects it and opens the sign-in). Nothing else: the
installer carries an embeddable Python with the skill's packages, and a copy of
the skill that it installs into `%USERPROFILE%\.claude\skills\courseforge` on
first run and refreshes on upgrade — a skill folder it did not install (a
designer's live copy) is left alone.

Work lives in `Documents\CourseForge-Assistant\<course id>\` in the skill's own
layout — `canvas.config.<id>.json` beside a DPAPI-encrypted `canvas.token.enc`,
written by Python in the exact format `CanvasToken.ps1` reads — so every script
resolves the course with no extra flags and a designer can drive the same
folder by hand. Console mode for diagnosis and scripting:
`courseforge-assistant ask <course_id> "<request>"`.

Install and first-use instructions:
[`installer/INSTALL-GUIDE-ASSISTANT.md`](installer/INSTALL-GUIDE-ASSISTANT.md).

### Building it

```powershell
cd installer
.\Build-Assistant.ps1            # tests -> verified Python download -> hash-locked packages -> PyInstaller -> stage -> smoke -> Inno Setup
.\Build-Assistant.ps1 -SkipPython -SkipInstaller   # quick rebuild of the exe only
```

The exe is small (no PDF engine inside it); `python\` and `skill\` are staged
beside it. Output is not committed — ship the setup exe as a GitHub Release.

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
python skill\scripts\pdf_fastlane.py selftest              # PDF engine + alt pipeline
python skill\scripts\test_restyle_html.py                  # the restyler's verify gate
python skill\scripts\test_courseforge_assistant.py         # permission gate, hook, token format, stream parsing
```

All three are dependency-free and must pass before any change ships.

---

## Repository layout

```
skill/          the courseforge Claude Code skill - THE SOURCE
  SKILL.md        playbook
  brand.json      palette + fonts for generated markup
  references/     detailed guides (ADA remediation, style, calendar, SLO...)
  scripts/        22 PowerShell verbs + 12 Python tools, plus the two desktop apps:
                  courseforge_gui.py (PDF Fixer), courseforge_assistant.py (Assistant),
                  cf_theme.py (shared look), cf_assistant_hook.py (permission gate)
installer/      PyInstaller specs, Inno Setup scripts, icons, install guides,
                Build-Assistant.ps1 (one-command Assistant build)
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
