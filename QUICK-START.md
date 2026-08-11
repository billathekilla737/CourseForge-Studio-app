# ⚡ Quick Start — no terminal skills needed

You'll do this once, and after step 2 you never touch PowerShell again — everything
happens inside the Claude Code app.

## 0. Install the Claude Code app (skip if you have it)

Download **Claude Code** (the desktop app) from
[claude.com/claude-code](https://claude.com/claude-code), run the installer like any
program, and **sign in with your Claude account**. The claude.ai *website* won't work
for this — it can't load plugins; you need the app.

## 1. Install CourseForge — paste one line

Press the **Windows key**, type `powershell`, press Enter. In the blue window, paste
this line (right-click pastes) and press Enter:

```powershell
irm https://raw.githubusercontent.com/billathekilla737/CourseForge/main/bootstrap.ps1 | iex
```

You don't need to understand it — it installs **both** tools (`courseforge`, which
builds and fixes course content, and `canvas-pii-guard`, the safety layer that blocks
student-data requests), wires up the safety hooks, and checks itself. Watch for
**ALL TESTS PASSED** / **DONE**, then close PowerShell for good.

> Re-running that same line later is how you **update**. Always safe.

**If it says it found a "Microsoft Store Python stub"** — that's Windows pretending to
have Python when it doesn't. The install still finished (you'll still see **ALL TESTS
PASSED**); you just can't fix PowerPoints, Word docs, or PDFs yet. To turn that on, paste
this, then re-run the line above **in a new PowerShell window**:

```powershell
winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
```

Building pages, quizzes and modules works fine either way, so you can skip this and come
back to it.

## 2. Restart Claude Code

Fully quit and reopen the app (plugins and safety hooks only switch on at startup).
If a **trust prompt** appears, read it and approve — that's normal.

## 3. Check it worked

Ask Claude, in the app:

> *Is canvas-pii-guard active, and do you have the courseforge skill?*

It should confirm **both**. If not: re-run step 1, restart again.

## 4. Connect your Canvas

In the app, choose **Open Folder** and pick — or create — this folder:

```
C:\Users\<you>\Documents\canvas-work
```

The install line prints this exact path when it finishes — copy it from there if you're
unsure.

**What's this folder?** It's just the place your Canvas connection gets saved.
Nothing special lives there ahead of time — but always open this same folder, and
you'll always be connected without setting anything up twice.

> **If you use OneDrive, watch this one.** Many school laptops have OneDrive back up your
> Documents, which means you may have *two* folders both called "Documents":
> `C:\Users\<you>\Documents` and `C:\Users\<you>\OneDrive\Documents`. Either one works —
> but the **non-OneDrive** one is the better choice, because your Canvas access token gets
> saved in this folder, and a token can do anything your Canvas account can do. Keeping it
> off OneDrive keeps it off the cloud.

Then say:

> *Set up my Canvas.*

It asks two things:

- **Your course web address** — the link in your browser, like
  `https://yourschool.instructure.com/courses/12345`
- **Your access token** — typed hidden, like a password. To get one: in Canvas go to
  **Account → Settings → + New Access Token → Generate**, then paste it when asked.

Done. Now you can say things like *"Bring this course up to ADA compliance,"*
*"Give this course the school look,"* *"Replace the old instructor's contact info
everywhere — including inside the PowerPoints and PDFs,"* or *"Export this course
as a backup."*

---

**Trouble?**
- If your IT setup blocks the one-liner: download the repo ZIP from
  [github.com/billathekilla737/CourseForge](https://github.com/billathekilla737/CourseForge)
  (green **Code** button → Download ZIP), extract it, right-click `Install-CourseForge.ps1`
  → **Run with PowerShell**. Then continue from step 2.
- An AI assistant can drive the install for you — see
  **[AGENT-INSTALL-PROMPT.md](AGENT-INSTALL-PROMPT.md)**.
- **Terminal person?** The CLI works too (`claude` from your course folder — the
  bootstrap even fixes the CLI's PATH if its installer forgot). The desktop app and
  CLI share the same install.
- To uninstall later: run `Uninstall-CourseForge.ps1` from the repo (your Canvas
  tokens/configs are left alone).
