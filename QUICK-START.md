# ⚡ Quick Start (3 minutes)

You'll do this once. You need the **Claude Code** app (desktop, CLI, or the VS Code
extension) — not the claude.ai website, which can't load plugins.

## 1. Install — paste one line

Open **PowerShell** (press the Windows key, type `powershell`, press Enter) and paste
this line, then press Enter:

```powershell
irm https://raw.githubusercontent.com/billathekilla737/garris-canvas-tools/main/bootstrap.ps1 | iex
```

It installs **both** tools — `courseforge` (builds and fixes course content) and
`canvas-pii-guard` (the safety layer that blocks student-data requests) — registers the
safety hooks, and checks itself with the built-in test suite. Watch for
**ALL TESTS PASSED** near the end.

> Re-running the same line later is how you **update**. It's always safe to re-run.

## 2. Restart Claude Code

Fully close and reopen Claude Code. Plugins and their safety hooks only switch on at
startup. If a **trust prompt** pops up, read it and approve — that's normal.

## 3. Check it worked

Ask Claude:

> *Is canvas-pii-guard active, and do you have the courseforge skill?*

It should confirm **both**. If not, re-run the line from Step 1 and restart again.

## 4. Connect your Canvas

Open Claude Code in a folder for your course and say:

> *Set up my Canvas.*

It asks two things:

- **Your course web address** — the link in your browser, like
  `https://yourschool.instructure.com/courses/12345`
- **Your access token** — typed hidden, like a password. To get one: in Canvas go to
  **Account → Settings → + New Access Token → Generate**, then paste it when asked.

That's it — you can now say things like *"Bring this course up to ADA compliance,"*
*"Build my Week 1 page,"* or *"Add a final exam to this course."*

---

**Trouble?**
- If your IT setup blocks the one-liner, download the repo ZIP from
  [github.com/billathekilla737/garris-canvas-tools](https://github.com/billathekilla737/garris-canvas-tools)
  (green **Code** button → Download ZIP), extract it, right-click `Install-CourseForge.ps1`
  → **Run with PowerShell**.
- An AI assistant can drive the install for you — see
  **[AGENT-INSTALL-PROMPT.md](AGENT-INSTALL-PROMPT.md)**.
- To uninstall later: run `Uninstall-CourseForge.ps1` from the repo.
