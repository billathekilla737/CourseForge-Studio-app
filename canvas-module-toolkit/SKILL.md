---
name: canvas-module-toolkit
description: >-
  Update Canvas LMS course modules — restyle page/assignment/quiz HTML to an
  institution's brand, refresh content for currency, and fix quiz scoring or factual
  errors. Dry-run-first, content-endpoints-only (never touches rosters/grades/
  submissions). Use when asked to update, restyle, refresh, or "make more relevant" a
  Canvas module's notes page, assignment, or quiz.
---

# Canvas Module Toolkit

The full procedure lives in **[AGENTS.md](AGENTS.md)** in this same folder — read it
before doing anything. It is written to be agent-agnostic (it is also read directly by
Codex and 25+ other tools that support the AGENTS.md standard), so there is nothing
Claude-specific to add here beyond: use your normal Bash/Read/Write/WebSearch tools to
carry out the shell commands and file edits AGENTS.md describes, and use
`AskUserQuestion` for any judgment call it tells you to surface rather than decide
silently (publish state, whether to touch a non-unpublished course, etc.).

Scripts: `scripts/`. Reference docs and the example style guide: `references/`.
Templates: `examples/`.
