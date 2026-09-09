# CourseForge Assistant — install & first use

*For the person setting up the PCs, and for the instructors and designers using it.*
*Current version: 0.2.0*

CourseForge Assistant is the second CourseForge desktop app. Where the PDF
Fixer has six buttons for one job, the Assistant has one box you type into:
say what you want done to your Canvas course, in your own words, and it does
it — with Claude Code working behind the window and you approving every change
to Canvas in a dialog.

## Installing (per PC, ~3 minutes)

1. Copy `CourseForge-Assistant-Setup-0.2.0.exe` to the PC (USB stick or a
   shared drive is fine).
2. Double-click it and click Next, Next, Finish. **No admin password is
   needed** — it installs into the user's own profile, with a Start Menu entry
   and a desktop icon.
3. If Windows shows **"Windows protected your PC"**, stop and check with IT
   rather than clicking through: the copy was downloaded by a browser and is
   not signed. Signed builds and IT-pushed installs do not show it.

Python and the CourseForge toolkit are inside the app. The one thing that is
**not** inside it is Claude Code, which signs in with the person's own Claude
account:

4. **IT installs Claude Code** on the PC (it is a standard per-user Anthropic
   package; put it in the image or push it the same way as the Assistant).
   Instructors should not be asked to paste install commands into PowerShell.

5. Run `claude` once, sign in when the browser opens, then close the window.
   (If this step is skipped, the Assistant offers to open the sign-in the
   first time Send is pressed.)

## First use (what to tell the instructor)

Open **CourseForge Assistant** from the Start Menu or desktop. The window has a
course picker across the top, a row of suggestions, a conversation pane, and a
box to type in at the bottom.

1. **Connect a course…** (top right) — paste the course web address from the
   browser, then a Canvas access token (Canvas → Account → Settings → New
   Access Token). One token covers every course on that Canvas site; it is
   stored encrypted, for that Windows account on that PC only. If the PDF
   Fixer already has a token saved on this PC, leave the token box blank.
   Give the token an expiry date when you create it.
2. **Say what you want.** Type it in the box and press Enter (Shift+Enter for a
   new line), or click one of the suggestions to start from a ready-made
   request — *Make it ADA compliant*, *Give it the school look*, *Add
   content*, *Fix PowerPoints & Word files*, *Back up / export*, *Check SLO
   alignment* — and edit it before sending.
3. **Watch the pane.** Claude explains what it is doing in plain language, and
   every step it takes appears as a short grey line. It reads and prepares
   freely; it will ask you a question when it needs a decision (for example,
   published or unpublished) and wait for your answer in the same box.
4. **The Allow / Deny dialog.** Before anything changes in Canvas, a dialog
   opens naming the course, what is about to happen (as the app itself reads
   the command, with Claude's own description shown separately), and the exact
   command. **Allow** lets that one step run. **Deny** stops it; Claude will
   explain what it was about to do and ask what you want instead. Closing the
   dialog counts as Deny, and so does leaving it unanswered for twenty minutes.
   The app also asks before Claude runs a script that is not part of the
   CourseForge toolkit, writes a script or settings file, contacts a website
   other than your Canvas site, or changes something on the PC. Reads, dumps,
   dry runs and the toolkit's own scripts run without asking.

`Open course folder` at the bottom opens the folder holding this course's
working files, reports and a plain-text copy of the conversation.

## What the dialog will and will not ask about

The Assistant approves on its own: reading the course, downloading content,
restyling and checking files on this PC, and every script run in dry-run mode
(without `-Apply`). It asks you before:

- any script run with `-Apply`, and the three that write as soon as they run
  (`Push-CanvasPages`, `Push-CanvasProject`, `Trim-CanvasNav`)
- any direct web request that changes data (PUT, POST, DELETE)
- deleting folders, installing software, or changing Windows settings
- editing a file outside the course's own folder

If the Assistant window is not running to ask, the answer is always **no** —
the change does not happen.

## Notes for the deployer

- **Per-user install.** Each Windows account that will use it runs the
  installer once. For an all-users or Intune/SCCM push, ask about an MSI.
- **One named Windows account per person.** The saved Canvas token and the
  Claude sign-in only unlock for the Windows account that created them. On a
  shared or generic login, the next person inherits both. Do not deploy to
  shared logins.
- **Signing.** Build with `Build-Assistant.ps1 -SignThumbprint <cert>` or
  `-SignCommand` (Azure Trusted Signing). Unsigned builds work, but SmartScreen
  warns on a browser-downloaded copy and AppLocker/WDAC publisher rules cannot
  allow them. Every build writes a `.sha256` next to the installer; publish it.
- **Claude Code** must be installed and signed in for that Windows account. The
  app finds it on PATH or in the usual native and npm locations. Usage bills to
  the person's own Claude plan (Pro or Max); no API key is involved.
- **Working files** live in `Documents\CourseForge-Assistant\<course id>\`, in
  the same layout the CourseForge scripts expect (`canvas.config.<id>.json`
  plus an encrypted `canvas.token.enc`), so a designer can also drive that
  folder from PowerShell or Claude Code directly. The `assistant\` subfolder
  holds the conversation log and the session id. A trace of which tools ran
  (names, commands and paths only - never tool output or student data) is
  kept under `%LOCALAPPDATA%\CourseForge-Assistant\logs\<course id>\`,
  useful when something goes wrong.
- **Tokens** are stored twice, both DPAPI-encrypted for that Windows account on
  that machine: a per-site copy in `%LOCALAPPDATA%\CourseForge-Assistant\`
  (so the person is asked once per PC; the PDF Fixer's copy is honoured too)
  and the per-course `canvas.token.enc` the scripts read. Nothing is written
  under OneDrive unless Documents itself is redirected there, and the app says
  so at startup if it is.
- **The skill.** The app ships the `courseforge` skill and installs it into
  `%USERPROFILE%\.claude\skills\courseforge` on first run, refreshing it when
  the app is upgraded. A skill folder that the app did not put there (a
  designer's live copy) is used as-is and never overwritten.
- **Python.** `python\` beside the exe is an embeddable CPython with
  python-pptx, python-docx, pypdf, pymupdf, pikepdf, lxml and fontTools. It is
  put first on PATH for Claude's session, so the skill's Python tools run on a
  PC with no Python installed. It also runs the permission hook.
- **Console mode.** `courseforge-assistant.exe ask <course_id> "<request>"`
  runs the same session in a console with y/n permission prompts — handy for
  scripted jobs and for diagnosing a PC over a remote session.
  `courseforge-assistant.exe connect <course url>` connects a course without
  the window; it asks for the token at a hidden prompt and refuses one on the
  command line (that would land in shell history).
- **Logs.** `Documents\CourseForge-Assistant\activity-log.jsonl` records
  connects, session starts, turns, permission decisions and errors — never
  tokens, prompts or student data. A startup failure also writes
  `startup-error.txt` there and shows a message naming it.

## Uninstalling

Settings → Apps → CourseForge Assistant → Uninstall. This also deletes the
saved Canvas token under `%LOCALAPPDATA%\CourseForge-Assistant`. Working
files in `Documents\CourseForge-Assistant` (including each course's
`canvas.token.enc`) and the installed skill are left in place; delete them by
hand if the PC is being handed over.
