# Install CourseForge (for instructors)

Plain, copy/paste instructions. **Works in Claude Code only** (the desktop app, CLI, or
IDE extension) — it does **not** work in the claude.ai website or Claude Desktop, because
those can't load plugins.

> **What you're installing:** two small plugins.
> - **courseforge** — builds your Canvas *course content* (pages, modules, assignments,
>   quizzes, syllabus). It never reads student rosters, grades, or submissions in normal use.
> - **canvas-pii-guard** — a local safety block that *enforces* that: it stops any
>   student-data request before it can run. **Install both** — courseforge alone leaves
>   the protection off.

---

## Step 1 — Install (type these 3 lines in Claude Code, one at a time)

```
/plugin marketplace add billathekilla737/garris-canvas-tools
/plugin install courseforge@garris-canvas-tools
/plugin install canvas-pii-guard@garris-canvas-tools
```

- You'll see a **trust prompt** — that's normal. Review and approve it.
- These start with a slash (`/`) and must be **typed by you** — an AI assistant cannot
  run slash commands for you.

> **If `/plugin` says "isn't available in this environment":** you're on a surface that
> can't load the plugin marketplace (the Claude Agent SDK harness, automation/headless
> runs, etc.). Skip to **[Alternative install](#alternative-install-no-plugin-the-script-method)**
> below — there's a one-script installer an AI assistant *can* run for you.

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

## Alternative install (no `/plugin`): the script method

Use this when the `/plugin` commands aren't available — for example inside the **Claude
Agent SDK harness** or any automation where typing `/plugin` returns *"isn't available in
this environment."* It does the same job as the marketplace install, including the part a
plain folder-copy never could: it **registers the canvas-pii-guard hooks** into your
Claude `settings.json`, so the safety block is never left off.

An AI assistant **can run this for you** (it's regular commands, not slash commands).
Paste this into Claude Code:

> Install the **garris-canvas-tools** plugins using the script method, because `/plugin`
> isn't available here. Clone `https://github.com/billathekilla737/garris-canvas-tools`
> to a temp folder and run `Install-CourseForge.ps1` from it. Then show me the test
> result (it should say 51 passed) and confirm both the courseforge skill and the
> canvas-pii-guard hooks are installed. Finally, help me connect my Canvas.

Or run it yourself in PowerShell:

```powershell
git clone https://github.com/billathekilla737/garris-canvas-tools "$env:TEMP\garris-canvas-tools"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\garris-canvas-tools\Install-CourseForge.ps1"
```

The installer is **idempotent** (safe to re-run), **merges** into any existing
`settings.json` without disturbing your other settings (it writes a `settings.json.bak`
first), and finishes by running the bundled **51-check** guard test suite so you can see
the block is active. If you're in a normal Claude Code app afterward, **restart** so the
hooks load; in the SDK harness they're picked up without a restart. Then do **Step 4**
above to connect Canvas.

> Requires Windows PowerShell 5.1+ and `git`. Installs to `%USERPROFILE%\.claude`
> (override with `-ConfigDir`).
