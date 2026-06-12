# ⚡ Quick Start (5 minutes)

You'll do this once. You need the **Claude Code** app (desktop, CLI, or the VS Code
extension) — not the claude.ai website, which can't load plugins.

## 1. Install the tools

Type these three lines into Claude Code, **one at a time**, pressing Enter after each.
(They start with `/` — you have to type them yourself; an assistant can't run slash
commands for you.)

```
/plugin marketplace add billathekilla737/garris-canvas-tools
/plugin install courseforge@garris-canvas-tools
/plugin install canvas-pii-guard@garris-canvas-tools
```

A **trust prompt** will pop up — that's normal. Read it and approve.

> **Install both.** `courseforge` builds your course content; `canvas-pii-guard` is the
> safety layer that blocks student-data requests. courseforge alone leaves the
> protection off.

## 2. Restart

Fully close and reopen Claude Code. Plugins and their safety hooks only switch on at
startup.

## 3. Check it worked

Ask Claude:

> *Is canvas-pii-guard active, and do you have the courseforge skill?*

It should confirm **both**. If the guard isn't on, run the third line from Step 1 again
and restart.

## 4. Connect your Canvas

Open Claude Code in a folder for your course and say:

> *Set up my Canvas.*

It asks two things:

- **Your course web address** — the link in your browser, like
  `https://yourschool.instructure.com/courses/12345`
- **Your access token** — typed hidden, like a password. To get one: in Canvas go to
  **Account → Settings → + New Access Token → Generate**, then paste it when asked.

That's it — you can now say things like *"Build my Week 1 page"* or *"Add a final exam
to this course."*

---

**Trouble?** If `/plugin` says *"isn't available in this environment,"* you're on a
surface that can't load the plugin marketplace (the Claude Agent SDK harness,
automation/headless runs). See **[Alternative install (the script method)](AGENT-INSTALL-PROMPT.md)**
— there's a one-script installer an AI assistant can run for you.
