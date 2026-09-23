# Studio state in Canvas user files

CourseForge Studio keeps a replica of its standing state in **your** Canvas
files, under **Files → courseforge-studio → state**. A second computer signed
in as you hydrates from that folder on startup and after you open the
accommodations roster.

This is separate from:

- `courseforge-studio/record` — the tamper-evident audit chain (upload-only)
- `canvas-grader/` — grading handoff for one assignment

## What travels

| Key | What it is |
|---|---|
| `accommodations.json` | Standing accommodation roster |
| `students-<uid>.json` | Instructor notes on that student (slashes flatten to dashes) |
| `assistant-<cid>.json` | That course's Assistant transcript, mode and model |
| `build-<cid>.json` | Unpublished Build drafts, plus the saved manifest, rubrics and `state.json` |
| `nicknames.json` | Instructor nicknames, keyed by Canvas user id. Display only |
| `attendance-<cid>.json` | Attendance marks for that course, keyed by Canvas user id. No names |

Applied quiz extras and `Extension:` deadline overrides already live in Canvas
itself. They do not need this replica.

Assistant chats are stored with student **tags**, not names. Opening Assistant
on another computer shows the transcript; Claude's own session id does not
resume there, so the next Send starts a new session on top of that history.
A live Claude session on this computer is not overwritten by a pull.

## What does not travel

- The Canvas token (`canvas.token.enc`, DPAPI, this machine)
- `names.json` / `map.json` (rebuilt from Canvas user ids)
- Raw submissions, a11y working copies, Build `backups/`
- Assistant `settings.json` (the Allow/Deny hook path is this machine's)

## Conflicts

If both computers edit the same key before either replica lands, the matching
screen (roster, Assistant, or Build) asks which copy to keep. Nothing is
overwritten silently.

## Config

```json
"state_to_canvas": true,
"state_sync_s": 90
```

`allow_canvas_writes: false` still blocks the upload; hydrate (a read) still
runs.
