# Install CourseForge (for instructors and curriculum designers)

Plain, copy/paste instructions. **Works in Claude Code only** (the desktop app, CLI, or
IDE extension) — it does **not** work in the claude.ai website or Claude Desktop, because
those can't load plugins.

> **What you're installing:** two small plugins.
> - **courseforge** — builds and remediates your Canvas *course content* (pages, modules,
>   assignments, quizzes, syllabus, PPTX). It never reads student rosters, grades, or
>   submissions in normal use.
> - **canvas-pii-guard** — a local safety block that *enforces* that: it stops any
>   student-data request before it can run. **Install both** — courseforge alone leaves
>   the protection off.

---

## Step 1 — Install (one line in PowerShell)

Open **PowerShell** and paste:

```powershell
irm https://raw.githubusercontent.com/billathekilla737/CourseForge/main/bootstrap.ps1 | iex
```

It installs both plugins (via the Claude Code plugin system when available, else the
bundled script installer), registers the safety hooks, sets up `python-pptx`, and runs
the guard test suite. **Safe to re-run any time — re-running is also the update path.**

An AI assistant **can run this line for you** (it's a regular command, not a slash
command). Paste this into Claude Code:

> Install the **garris-canvas-tools** plugins by running the one-line bootstrap:
> `irm https://raw.githubusercontent.com/billathekilla737/CourseForge/main/bootstrap.ps1 | iex`
> Then show me the test result (it should say ALL TESTS PASSED) and confirm both the
> courseforge skill and the canvas-pii-guard hooks are installed. Finally, help me
> connect my Canvas.

> **Prefer the in-app marketplace?** Type these three lines in Claude Code yourself
> (slash commands can't be typed for you), approving the trust prompts:
> ```
> /plugin marketplace add billathekilla737/CourseForge
> /plugin install courseforge@garris-canvas-tools
> /plugin install canvas-pii-guard@garris-canvas-tools
> ```

## Step 2 — Restart

**Fully close and reopen Claude Code.** Plugins and their safety hooks only load at startup.

## Step 3 — Check it worked

Ask Claude:

> *Is canvas-pii-guard active, and do you have the courseforge skill?*

It should confirm the **courseforge** skill is available **and** that the
**canvas-pii-guard** PreToolUse hook is registered. If the guard isn't active, re-run the
third command from Step 1 and restart again.

## Step 4 — Connect your Canvas

Open Claude Code in a folder for your course work and say:

> *Set up my Canvas.*

It asks two things — your **course web address** and your **access token** (typed hidden,
like a password) — and does the rest. To get a token: in Canvas, go to
**Account → Settings → New Access Token**, generate it, and paste it when asked.

---

## Optional: have Claude guide you through it

If you'd rather be walked through Steps 1-4, paste this into Claude Code:

> Guide me through installing the **garris-canvas-tools** plugins from the marketplace.
> I know that `/plugin` commands are slash commands I have to type myself — give them to
> me one at a time and wait while I run each one. After I restart, verify that **both**
> the courseforge skill and the canvas-pii-guard hooks are active, tell me if either is
> missing, and then help me connect my Canvas (set up my token).

That assistant can't press the keys for the `/plugin` commands, but it can hand them to
you in order, confirm the install afterward, and run the Canvas setup with you.

---

## Under the hood (and offline fallback)

The bootstrap prefers the Claude Code plugin system; when the CLI isn't available it
downloads this repo as a **ZIP** (no git required) and runs `Install-CourseForge.ps1`,
which does the part a plain folder-copy never could: it **registers the canvas-pii-guard
hooks** into your Claude `settings.json`, so the safety block is never left off.

If your network blocks `raw.githubusercontent.com`, do it by hand: download the repo ZIP
from GitHub (green **Code** button → Download ZIP), extract it, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-CourseForge.ps1
```

The installer is **idempotent** (safe to re-run), **merges** into any existing
`settings.json` without disturbing your other settings (it writes a `settings.json.bak`
first), and finishes by running the bundled guard test suite (watch for
**ALL TESTS PASSED**) so you can see the block is active. If you're in a normal Claude
Code app afterward, **restart** so the hooks load; in the SDK harness they're picked up
without a restart. Then do **Step 4** above to connect Canvas.

To remove everything later, run `Uninstall-CourseForge.ps1` from the repo — it
de-registers the hooks and deletes the skills, leaving your Canvas tokens/configs alone.

> Requires Windows PowerShell 5.1+. Installs to `%USERPROFILE%\.claude`
> (override with `-ConfigDir`).
