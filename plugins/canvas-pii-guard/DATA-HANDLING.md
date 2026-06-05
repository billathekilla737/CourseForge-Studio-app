# canvas-pii-guard — Data Handling Brief (for IT / administration / legal review)

## Purpose
This component is a **local, technical control** that prevents Canvas **student PII**
from being acquired by, or transmitted from, an instructor's machine when they use the
`courseforge` content skill with Claude Code. It exists so adoption does **not**
rest on "we trust the AI" — the protection is enforced on the instructor's computer,
independent of the model.

## What we are protecting against
The Canvas API token an instructor holds can technically read rosters, grades, and
submissions. The risk is that such data could be fetched and sent to the model
provider (Anthropic) during normal use. The goal: student PII is **never fetched**, so
there is nothing to transmit.

## Data flow (with the guard in place)
```
Instructor's PC                                   |  Anthropic servers
--------------------------------------------------|---------------------
Claude Code agent wants to call a tool            |
   |                                              |
   v                                              |
[ canvas-pii-guard PreToolUse hook ] -- runs LOCALLY, before the call
   |  - student-data endpoint or private/ read?  -> DENY (call never runs)
   |  - course CONTENT endpoint?                 -> allow
   v                                              |
tool runs (content only) --> result --> [ PostToolUse redactor (backstop) ]
                                              |   |
                                              v   v
                                       sanitized result --> sent to model -->  (model)
```
Blocked calls never execute, so no roster/grade/submission data leaves the machine.

## Layered controls (defense in depth)
1. **AI policy (hard stop).** The content skill's instructions refuse student-data
   requests, even under pressure. *(Policy layer — necessary but not sufficient alone.)*
2. **No PII tools.** The content skill ships **zero scripts** that read rosters, grades,
   or submissions. Auditable by reading its `scripts/` folder. *(Removes the capability.)*
3. **PreToolUse BLOCK hook (the guarantee).** `guard-block.ps1` inspects every
   Bash/PowerShell/WebFetch call locally and **denies** any that target Canvas
   student-data endpoints (`/enrollments`, `/users`, `/students`, `/submissions`,
   `/gradebook`, `/grades`, `/analytics`, `/quiz_submissions`, `/conversations`, …) or
   that read a local student-data cache (`private/`, `grading/`). The **only**
   exceptions are the three sanctioned gateway scripts named in layer 4, matched by
   name; every other command remains denied. **Fail-closed:** unrecognized Canvas
   endpoints are denied. *(Enforced locally, before the network.)*
4. **Sanctioned gateways.** If data is ever genuinely needed, the policy allows three
   scripts **by name** (and nothing else); each keeps raw data local and emits only a
   reduced-risk rendering:
   - `Get-CanvasData-Sterilized.ps1` — general read: keeps the **raw** response in a
     gitignored `private/` folder the agent never reads, emits only a **pattern-redacted**
     rendering (Strict profile).
   - `Build-GradingBundle.ps1` — **opt-in blind grading** (in the `courseforge`
     content skill): pulls submission **text only**, writes the pseudonym->identity
     `map.json` to a gitignored `grading/` folder the model never reads, and emits a
     **scrubbed + pseudonymized** `bundle.json` (PII patterns + the student's own name
     tokens removed; attachment contents are never downloaded, only filenames listed).
   - `Post-Grades.ps1` — posts grades back **by pseudonym**, resolving identities from the
     local `grading/` map (dry-run by default, audited). Because it legitimately reads the
     local `grading/` map, the policy permits these three scripts **before** the
     local-cache block and the `/submissions` deny; **every other** command hitting those
     endpoints or caches remains blocked fail-closed.
   The de-identification in the grading gateways is **best-effort**, not a guarantee
   (free-text and screenshot-embedded PII can remain) — see the BEST-EFFORT note below.
5. **PostToolUse redactor (backstop).** `guard-redact.ps1` scrubs structured PII
   (emails, phone numbers, SIS/9-digit IDs, Canvas user-id URL params, PII JSON fields)
   from tool output. *Version-dependent; treat as a net, not the guarantee.*

## What is GUARANTEED vs BEST-EFFORT (please read)
- **Guaranteed and auditable — PREVENTION.** With layers 2 + 3, the skill does not
  fetch student data, so there is nothing to transmit. This is verifiable: read
  `guard-block.ps1` (the deny/allow logic is plain text), audit the skill's `scripts/`
  (no student-data calls), and run the test harness. This is the claim to stand behind.
- **Best-effort — REDACTION.** Pattern matching (gateway + redactor) reduces risk for
  *incidental* structured PII. It **cannot be certified "100% / no leaks"** for
  arbitrary free-text PII (an unusual name in prose, text inside an uploaded PDF/image,
  file metadata). **Do not represent regex redaction as a guarantee** — that would be
  inaccurate and a liability. The guarantee is prevention, not redaction.
- **Known limitations (full disclosure).**
  - The block hook inspects the agent's *tool-call text*; it does not introspect the
    internals of arbitrary third-party scripts. This is mitigated by layer 2 (the
    content skill ships no PII tools) — which is why both layers matter together.
  - Free-text student names without a roster are not reliably redactable (hence the
    reliance on prevention).
  - PostToolUse output-rewriting is version-dependent in Claude Code — verify before
    relying on it; the block hook does not depend on it.

## Enforcement / deployment
- **Pilot (current):** the guard ships as a **plugin**; installing it activates the
  hooks. A user could uninstall it — acceptable for a pilot, **not** for an institutional
  guarantee.
- **Institution-grade (recommended for rollout):** deploy the block hook via
  **managed settings** so it is admin-owned and users **cannot disable** it
  (Windows: a machine-level `managed-settings.json`; can be pushed via Group Policy /
  Intune). This is what converts the control from "opt-in" to "enforced."
- **Do NOT install this guard on admin/grading machines** that legitimately access
  student data (e.g., the separate `courseforge-admin` grading tool) — it would
  block their sanctioned work.

## How to verify / audit (for the reviewer)
1. **Run the evidence harness:** `tests/Run-GuardTests.ps1` → produces
   `guard-coverage-report.txt` (PII endpoints blocked, content allowed, structured PII
   redacted, content not over-redacted). Synthetic data only.
2. **Read the policy:** `scripts/PiiPatterns.ps1` (`Test-CanvasCallAllowed`) and
   `scripts/guard-block.ps1` — short, plain, no network, no secrets.
3. **Audit the content skill:** confirm its `scripts/` make no roster/grade/submission
   calls.
4. **Optional:** run with a network monitor and attempt a roster call — it is denied
   locally before any request is made.

## Credentials (the strongest real control)
The most robust safeguard is the **Canvas token's role**: issue instructor tokens from
a Canvas role **without** "view grades / view all students / view submissions"
permissions. Then PII access is impossible at the source, regardless of software.
The guard is the software-side layer that complements this. Rotate any token on
suspected compromise.

## Residual risk & incident response
- Residual risk is concentrated in *deliberate misuse via the sanctioned gateway* or
  *novel free-text PII in content the instructor themselves pastes*. Mitigate with the
  scoped token (above) and managed enforcement.
- On suspected exposure: delete/rotate the Canvas token (Canvas → Settings → Approved
  Integrations), and review the agent transcript.

## Summary for sign-off
- The defensible, auditable guarantee is **prevention**: student data is not fetched
  (no PII tools + locally-enforced block), so it cannot be transmitted.
- Redaction is a clearly-labeled secondary net, **not** a 100% claim.
- For institution-wide assurance, enforce the block hook via **managed settings** and
  issue **scoped Canvas tokens**.

## Companion documents
- `Security-Overview.pdf` - plain-language, chart-based overview for non-technical
  reviewers / administration.
- `CONTINGENCY-ANALYSIS.md` - the full adversarial contingency matrix (every path PII
  could take, covered vs gap, severity, mitigation) and the hardening roadmap.
- `tests/guard-coverage-report.txt` - generated evidence (51 protection checks pass;
  disclosed limitations).
