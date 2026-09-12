# Blind / pseudonymized grading (OPT-IN)

This is the **only** sanctioned way to grade student submissions from the
`courseforge` skill. It is designed so that **student identities stay on the
local machine** and the model only ever sees pseudonymized, scrubbed submission text.

It is an opt-in flow, not normal use. For grading where the real identities are in
front of the model, use the separate `courseforge-admin` skill instead.

## The workflow

```
Build-GradingBundle.ps1   -> grading\<AssignmentId>\map.json   (LOCAL identities; gitignored)
                          -> grading\<AssignmentId>\bundle.json (scrubbed text; grade from THIS)
   |
   v
grade bundle.json by pseudonym  ->  grading\<AssignmentId>\proposed-grades.json
   |                                  [ { "id": "S-001", "score": 88, "comment": "..." }, ... ]
   v
Post-Grades.ps1            (dry-run: prints "would post S-001 -> user <id>: score, comment")
Post-Grades.ps1 -Apply     (resolves pseudonym -> user_id via map.json, PUTs the grade)
```

### 1. Build the bundle (sterilizing + pseudonymizing gateway)

```powershell
scripts\Build-GradingBundle.ps1 `
  -ConfigPath .\canvas.config.<id>.json `
  -AssignmentId 67890
```

It GETs `/courses/:id/assignments/:aid/submissions?include[]=user` (paginated), sorts
students by Canvas user id for deterministic pseudonyms (`S-001`, `S-002`, ...), and
writes, next to the config, under `grading\<AssignmentId>\`:

- **`map.json`** = `{ "S-001": { user_id, name, login_id }, ... }` — the pseudonym ->
  identity map. This is the **only** place identities live. It is **gitignored**, must
  **never** be read into the model, and must **never** be committed.
- **`bundle.json`** = `[ { "id": "S-001", "text": <scrubbed body>, "files": [<filenames>],
  "attachments_text": [...]? }, ... ]` — the submission body scrubbed in a FIXED order:
  structured PII first (email / phone / SSN-shaped / SIS / login id / **bare 8-10 digit
  runs** -> `[ID]`), then names — **every roster student's full-name forms** (so a peer
  mention "I worked with Bob Smith" is caught, not just the author) plus the author's
  individual name tokens and `login_id` -> `[NAME]`. Order matters: names-first mangles
  the author's own email into `[NAME].[NAME]@...` which the email pattern then misses
  (found by test). The finished bundle is **re-verified** — with the canvas-pii-guard's
  own independent redactor when installed — and the build **fails (exit 2)** on residual
  hits unless `-Force`. **Read only `bundle.json` to grade.**

Two optional switches:
- **`-IncludeAttachmentText`** — downloads `.docx` / `.pdf` / `.txt` attachments LOCALLY
  (under `grading\<id>\attachments\`, gitignored), extracts their text
  (`extract_attachment_text.py`), scrubs it through the same pipeline, and includes it as
  `attachments_text` — this is what makes file-upload assignments gradeable. Images and
  every other type stay filename-only ON PURPOSE (screenshots carry names in title bars
  and headers; no text scrubber sees pixels). A scanned PDF that yields no text is
  reported as such, not guessed at.
- **`-KeepLongNumbers`** — numeric-heavy work (math/CS answers like `16777216`) would be
  eaten by the bare-digit rule; this relaxes it (bare 9-digit runs are still redacted)
  and the verify step relaxes to match.

The script prints a live-course warning if the course is published or has enrollments,
and appends an audit line to `canvas-admin-audit.log`.

### 2. Grade by pseudonym

Read `bundle.json` only. Score each `S-NNN` and write
`grading\<AssignmentId>\proposed-grades.json`:

```json
[
  { "id": "S-001", "score": 88, "comment": "Good use of the loop; tighten the edge case." },
  { "id": "S-002", "score": 72, "comment": "Method signature is off; see the rubric." }
]
```

### 3. Post the grades (dry-run first)

```powershell
scripts\Post-Grades.ps1 -ConfigPath .\canvas.config.<id>.json -AssignmentId 67890
# review the dry-run lines, then:
scripts\Post-Grades.ps1 -ConfigPath .\canvas.config.<id>.json -AssignmentId 67890 -Apply
```

Dry-run prints `would post S-001 -> user <id>: score, comment` for every row. With
`-Apply` it resolves each pseudonym to a `user_id` via `map.json` and PUTs
`/assignments/:aid/submissions/:user_id` with `submission[posted_grade]` +
`comment[text_comment]`. It **refuses** if any graded pseudonym is missing from the map
(so you cannot post to the wrong student), warns on live courses, and audits each apply.

## Hard rules

- **Never commit the local map.** `map.json` (and `bundle.json`, `proposed-grades.json`,
  the whole `grading\` folder, and `canvas-admin-audit.log`) are gitignored. Do not move
  them out, do not paste their contents into chat, do not echo identities.
- **Grade from `bundle.json` only.** Reading `map.json` into the model defeats the entire
  purpose. The `canvas-pii-guard` allows `Post-Grades.ps1` to read the map because it
  resolves identities locally and never emits them; a generic read of `grading\` stays
  blocked.
- **Images/screenshots are never scrubbed or inlined.** Without `-IncludeAttachmentText`,
  no attachment contents are downloaded — only filenames are listed. With it, only
  `.docx`/`.pdf`/`.txt` TEXT is extracted and scrubbed; images and everything else stay
  filename-only, because a screenshot may contain a name (Windows title bar, email
  header, signature) and no text scrubber sees pixels. Review those **locally**; do not
  send image contents to the model.
- **Run the bundle build in its OWN command.** The guard's sanction matches the script
  name anywhere in the command line, so a chained read (e.g. `...Build-GradingBundle.ps1
  ...; Get-Content grading\...\map.json`) rides through the exemption. One command per
  action keeps the `grading\` block meaningful.

## Honest statement of what this does and does not guarantee

This is **best-effort de-identification, not a guarantee.** The redactor catches
structured PII (emails, phone numbers, SSN-shaped runs, MGCCC login/SIS ids, bare
8-10 digit ids), every roster student's full-name forms, and the author's own name
tokens — and the finished bundle is re-verified before it is blessed. But **free-text
identification can remain**: a nickname the roster does not know, a third party they
mention, identifying CONTENT only one student could have written ("as the team's only
left-handed pitcher..."), or a name inside an image. There is no claim of an "air gap"
or "100% clean": the machine still uses the internet, and regex redaction cannot certify
arbitrary free text.

What you can stand behind: raw identities are written only to the local `map.json`
(gitignored, never emitted to the model), the model grades pseudonymized text, the
`canvas-pii-guard` block hook recognizes these two scripts as sanctioned gateways while
still blocking every other student-data access fail-closed, and posting is dry-run-first
and audited. For the strongest control, pair this with a **scoped Canvas token** whose
role cannot view grades/students/submissions outside what the gateway needs.
