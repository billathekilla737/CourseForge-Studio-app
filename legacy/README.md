# legacy/

What CourseForge Studio replaced, kept for reference until parity is verified.

| Folder | What it was | Where it lives now |
|---|---|---|
| `powershell/` | The 22 CourseForge verbs (Dump-CanvasContent, Push-CanvasRemediation, Fastlane-CanvasPdfs, Set-DueDates, ...) | Python modules under `courseforge/<area>/` and `python -m courseforge <area> <verb>` |
| `desktop/` | The two tkinter apps: the PDF Fixer (`courseforge_gui.py` + `courseforge_pdf.py`) and the Assistant (`courseforge_assistant.py` + `cf_theme.py`), plus their tests | The web UI: `#/c/<course>/pdf` and `#/c/<course>/assistant`; the permission gate is `courseforge/assistant/gate.py` |
| `installer/` | PyInstaller specs, Inno Setup scripts, icons, install guides for the desktop apps | Not built for the Studio yet; see `docs/DEPLOYMENT-SCHOOL.md` for the hosted path |
| `skill/SKILL.md` | The old Claude Code skill playbook that drove the PowerShell verbs | `skill/SKILL.md` at the repo root, rewritten for the Python verbs |
| `sync-skill.ps1` | Mirrored `skill/` to `~/.claude/skills/courseforge` | The Studio installs its skill itself on first Assistant use |
| `tools/repair-misplaced-alt.py` | One-off repair for a pre-1.1.8 PDF alt-text bug | Kept as history |

Nothing in here is imported by the Studio. It is frozen: fixes go into `courseforge/`.
