# Contingency Analysis — canvas-pii-guard (proof of concept)

## How to read this
**Threat:** student PII (rosters, grades, submissions) reaching Anthropic's servers
during an instructor's normal use of the content skill. Below we enumerate **every
path** that data could take, mark each Covered / Partial / Gap, give the severity if
unmitigated, and state the mitigation. Gaps are **disclosed on purpose** — an honest
analysis is what earns admin trust.

## A word on "air-gapped"
This is **not** a literal air gap. The computer still uses the internet, and Claude
Code still talks to Anthropic to do its job. What we built is a **local block that
severs the *student-data* path before it can leave the machine** — content still
flows, student data is stopped at the door. That is the accurate claim, and it is the
one to make to reviewers.

## Scope of the guarantee (what the PoC actually promises)
The guard makes the **common and casual** paths safe: if the agent (or the instructor)
directly asks for student data, the call is **denied locally before any network
request**, and saved student-data files cannot be read. Combined with the content
skill shipping **zero PII-reading tools**, this prevents **accidental and casual**
exposure — the realistic instructor-workflow risk. It is **not** a sandbox against a
*determined* user who deliberately obfuscates; that needs the hardening layers (a
scoped token, network egress control, managed enforcement).

## Contingency matrix
| # | Path PII could take | Guard behavior | Status | Severity | Mitigation (now / hardening) |
|---|---|---|---|---|---|
| 1 | Agent directly calls roster/grades/submissions API (Bash/PS/WebFetch) | PreToolUse **denies** before it runs | **Covered** | High | Built in |
| 2 | Agent reads a saved student-data file (`private/`, `grading/`) | **Denied** | **Covered** | High | Built in |
| 3 | Agent calls an *unrecognized* Canvas data endpoint | **Fail-closed deny** | **Covered** | Med | Built in |
| 4 | Course-content calls (pages/modules/quiz definitions) | Allowed (the job); contains no PII | By design | n/a | Content only |
| 5 | A stray ID/email inside an allowed output | PostToolUse redactor scrubs structured PII | **Partial** (best-effort; version-dependent) | Low-Med | Verify version; scoped token |
| 6 | Student data fetched **inside a script** the agent runs (`-File`, dot-source) | Hook sees only the command line, not the script's internals | **Gap** | High | **Scoped token** (the fix); skill ships no PII tools; egress control |
| 7 | **Obfuscated** command (`-EncodedCommand`, URL built at runtime) | Not decoded/inspected | **Gap** | High | Scoped token; egress control; managed policy |
| 8 | A **Canvas MCP** connector tool (if one is ever installed) | Not matched by the `Bash\|PowerShell\|WebFetch` matcher | **Gap** (only if such an MCP is present) | High | Don't connect a Canvas MCP on instructor PCs; or extend the matcher to its tool names |
| 9 | Instructor **pastes** a roster/PII into the chat prompt | `UserPromptSubmit` can block but **cannot redact**; not enabled in PoC | **Gap** | Med | Training; optional detect-and-block hook; (scoped token does not help here) |
| 10 | PII inside an **uploaded file** (PDF/image) the agent reads | Not parsed by the regex redactor; image text is invisible to it | **Gap** | Med | Don't feed student files to the content skill; handle only in the admin flow on an admin PC |
| 11 | **Notion** content containing student PII (`notion-fetch`) | `notion-fetch` not matched; course pages rarely contain PII | **Low/Gap** | Low | Keep student PII out of Notion course pages |
| 12 | Hook **script error** | `guard-block` fails **open** (so a bad payload can't brick all tooling) | **Gap** (edge) | Low | Hardening: fail-closed for Canvas-referencing text; integrity-check the scripts |
| 13 | User **uninstalls/disables** the plugin | Hooks stop running | **Gap** (PoC enforcement) | High | **Managed settings** (admin-enforced; users can't disable) |
| 14 | Token used **outside** this tool (other apps, theft) | Out of scope of the guard | n/a | High | **Scoped token**; rotation; institutional token management |
| 15 | Aggregate counts (`total_students`) | Allowed | Not PII | None | By design |

## What this proves — and what it doesn't
- **Proves:** the student-data path is blocked locally for direct/casual use; structured
  PII is redacted; content workflows are unaffected; over-redaction does not occur. All
  reproducible via `tests/Run-GuardTests.ps1` and readable in `scripts/`.
- **Does not prove:** protection against deliberate obfuscation (#6, #7), MCP/Notion
  side-channels (#8, #11), pasted PII (#9), uploaded-file PII (#10), or a user disabling
  the plugin (#13). Those close only with the hardening layers below.

## Hardening roadmap (down-the-line; NOT required for the PoC)
In priority order:
1. **Scoped Canvas token/role — highest impact.** Issue instructor tokens from a Canvas
   role *without* view-grades / view-students / view-submissions. PII becomes
   **unfetchable at the source**, closing #1, 2, 3, 6, 7, 8, 10, 11 at once. The software
   guard then becomes the belt to this suspenders.
2. **Managed-settings enforcement.** Deploy the block hook via machine-level managed
   settings so it cannot be disabled (#13), with managed-only hooks.
3. **Network egress control.** A local/proxy rule that allows only the model endpoint +
   Canvas *content* endpoints and blocks Canvas *data* endpoints at the network — defeats
   obfuscation (#6, #7) regardless of what the agent does.
4. **Matcher extension.** If a Canvas MCP is ever added, add its tool names to the hook
   matcher (#8).
5. **UserPromptSubmit detect-and-block.** Block a turn whose prompt contains roster-like
   PII (#9), tuned to avoid false positives.
6. **Fail-closed on error** for Canvas-referencing calls (#12); sign/lock the hook
   scripts and verify integrity on load.
7. **File-content scanning.** If student files must ever be handled, OCR/extract and
   redact inside a local-only gateway (#10) — better, never handle them in the content flow.

## Verification (reproduce the evidence)
- `tests/Run-GuardTests.ps1` → `guard-coverage-report.txt` (every security assertion in
  the suite PASSES — the exact count is printed in the report header, so it never goes
  stale here — plus the disclosed limitations).
- Read `scripts/guard-block.ps1` and `scripts/PiiPatterns.ps1` (short, plain, no network).
- Live: with the guard installed, attempt a roster call — it is denied locally before any
  request is made.
