# From canvas-grader and CourseForge to CourseForge Studio

CourseForge Studio is the successor to two tools by the same author:

- **canvas-grader** (github.com/billathekilla737/canvas-grader): a local web UI
  that drafts rubric-based grades with the Claude Code CLI.
- **CourseForge** (github.com/billathekilla737/CourseForge): a Claude Code skill
  of PowerShell and Python tools for ADA remediation, content building and
  course operations, plus two tkinter desktop apps (PDF Fixer, Assistant).

This repository was forked from CourseForge (its git history is here) and the
grader package was merged in. Both originals are now superseded.

## Where things went

| Before | Now |
|---|---|
| `canvasgrader/` package | `courseforge/` (same modules, same behaviour) |
| `Canvas Grader.vbs`, `run.cmd`, `python -m canvasgrader` | `CourseForge Studio.vbs`, `run.cmd`, `python -m courseforge` |
| `%APPDATA%\canvas-grader\canvas.token` (plaintext) | `%APPDATA%\CourseForge-Studio\canvas.token.enc` (DPAPI). The old file is still read and migrated the first time you save a token. |
| `data/<course>/<assignment>/...` | Same layout, under this repo's `data/`. Copy the old `data/` folder here, or set `data_dir` in config.json to the old path, to keep drafts. |
| Canvas user-files folder `canvas-grader` (cross-machine handoff) | Same folder name, so handoffs from the old tool keep working. |
| `skill/scripts/*.ps1` (22 verbs) | Python modules under `courseforge/<area>/`, run as `python -m courseforge <area> <verb>`; originals in `legacy/powershell/` |
| `skill/scripts/pdf_fastlane.py` | `courseforge/pdf/fastlane.py` (unchanged engine) |
| `skill/scripts/restyle_html.py`, `remediate_pptx.py`, `remediate_docx.py`, `office_text_tool.py`, `pdf_text_tool.py`, `triage_pdf.py`, `slo_framework_tool.py` | `courseforge/a11y/restyle.py`, `courseforge/docs/*.py`, `courseforge/courseops/slo_framework.py` |
| `skill/scripts/cf_assistant_hook.py` | `courseforge/assistant/gate.py` (same invariants, Python-verb vocabulary) |
| `courseforge_pdf.py`, `courseforge_gui.py`, `courseforge_assistant.py`, `cf_theme.py` | The web UI (`#/c/<course>/pdf`, `#/c/<course>/assistant`); sources in `legacy/desktop/` |
| `skill/references/*.md` | `courseforge/knowledge/*.md` (served to the Assistant as its skill references) |
| `skill/brand.json` | `courseforge/brand.json` |
| `installer/` | `legacy/installer/` (not built for the Studio) |
| `canvas-pii-guard` plugin (removed upstream) | `courseforge/canvas_policy.py`: the content-scoped Canvas client refuses student-data endpoints before sending. Stronger than the old text hook, because it sees the real request. |
| `Documents\canvas-work\canvas.config.<id>.json` | Not needed. Courses come from your Canvas enrollments in the picker. |
| `Documents\CourseForge-Assistant\<id>\`, `Documents\CourseForge-PDF\<id>\` | `data/<course>/assistant/` and `data/<course>/pdf/`. Their DPAPI token blobs are read for migration. |

## If you have the old canvas-pii-guard hook installed

The old CourseForge install registered a PreToolUse hook in `~/.claude/settings.json`
that blocks any shell command mentioning certain paths and Canvas endpoints. It
was written for the PowerShell toolkit and it will block ordinary Studio
commands run from Claude Code (including the Assistant). Remove its two
`guard-block.ps1` / `guard-redact.ps1` entries from `~/.claude/settings.json`
(or run the old `Uninstall-CourseForge.ps1`). The Studio enforces the same
policy in the Canvas client itself.

## What did not change

- Nothing reaches Canvas on one click: every write is refused once by the
  server and sent only when the exact same change is confirmed.
- Non-submissions are not zeros; curves never lower a score.
- Pseudonymisation is on by default; the identity map never leaves the machine.
- Dry run first; verify gates are load-bearing; publish state is asked, default unpublished.
