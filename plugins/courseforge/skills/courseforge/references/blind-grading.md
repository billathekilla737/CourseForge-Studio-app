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
- **`bundle.json`** = `[ { "id": "S-001", "text": <scrubbed body>, "files": [<filenames>] }, ... ]`
  — the submission body run through a PII redactor (email / phone / SIS / login id /
  9-digit patterns) AND with each student's own name tokens replaced by `[NAME]`. **Read
  only `bundle.json` to grade.**

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
- **Files/screenshots are not scrubbed.** Attachment contents are never downloaded or
  inlined — only filenames are listed. A screenshot or uploaded file may contain a name
  (Windows title bar, email header, signature, a name typed in the document). Review
  those **locally**; do not send file contents to the model.

## Honest statement of what this does and does not guarantee

This is **best-effort de-identification, not a guarantee.** The redactor catches
structured PII (emails, phone numbers, MGCCC login/SIS ids, 9-digit ids) and the
student's own roster-name tokens, but **free-text PII can remain** — an unusual name a
student writes in prose, a third party they mention, a name embedded in an image or
file. There is no claim of an "air gap" or "100% clean": the machine still uses the
internet, and regex redaction cannot certify arbitrary free text.

What you can stand behind: raw identities are written only to the local `map.json`
(gitignored, never emitted to the model), the model grades pseudonymized text, the
`canvas-pii-guard` block hook recognizes these two scripts as sanctioned gateways while
still blocking every other student-data access fail-closed, and posting is dry-run-first
and audited. For the strongest control, pair this with a **scoped Canvas token** whose
role cannot view grades/students/submissions outside what the gateway needs.
