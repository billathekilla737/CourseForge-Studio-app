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
