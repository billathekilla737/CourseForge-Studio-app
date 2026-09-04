# CourseForge PDF Fixer — install & first use

*For the person setting up the 10 PCs, and for the instructors using it.*
*Current version: 1.1.7*

## Installing (per PC, ~2 minutes)

1. Copy `CourseForge-PDF-Fixer-Setup-1.1.7.exe` to the PC (USB stick or a
   shared drive is fine — no internet needed to install).
2. Double-click it. If Windows SmartScreen shows "Windows protected your PC",
   click **More info → Run anyway** (the installer is unsigned; see note below).
3. Click Next, Next, Finish. **No admin password is needed** — it installs
   into the user's own profile, with a Start Menu entry and desktop icon.

That's the whole install. Python, Tesseract, Java and veraPDF are all inside
the app — nothing else to download.

## First use (what to tell the instructor)

Open **CourseForge PDF Fixer** from the Start Menu or desktop. It's a
window with buttons down the left side:

1. **Connect course** — paste the course web address from the browser,
   then a Canvas access token (Canvas → Account → Settings →
   New Access Token). One-time per course; the token is stored encrypted
   on this PC only.
2. **Back up & fix** — downloads the course's PDFs and repairs them on
   this computer. **Nothing in Canvas changes during this step.**
   Originals are kept. Takes seconds to a couple of minutes.
3. **Describe images with Claude** — writes real alt-text descriptions
   for pictures inside the PDFs, using the instructor's Claude account.
   First use opens a sign-in window (needs a Claude Max/Pro account
   signed into Claude Code on that PC). Optional but recommended before
   uploading.
4. **Upload to Canvas** — replaces the course files in place after a
   confirmation. Course links keep working; untouched copies stay on
   the computer.
5. **Prove compliance** — runs the PDF/UA-1 standards report (the real
   ISO standard, not just a scanner score).

Two situations the app hands to a human on purpose:

- **"N files need a person to look at them"** — password-protected, signed,
  or otherwise unusual PDFs are skipped, never damaged. Send the named
  `queue.json` file to your instructional designer.
- **"N images received a safe placeholder description"** — this appears
  when step 3 (Describe) hasn't been run. Run Describe, or send the
  printed folder path to the designer for the description pass.

## Notes for the deployer

- Per-user install: each Windows account that will use it runs the installer
  once. For all-users or Intune/SCCM push, ask about building the MSI variant.
- Unsigned binary: SmartScreen will warn on first run. A college code-signing
  certificate removes this — worth requesting from IT if this rolls out wider.
- Working files live in `Documents\CourseForge-PDF\<course id>\`. The Canvas
  token is stored DPAPI-encrypted in the user's Documents\CourseForge-PDF
  folder and only unlocks for that Windows account on that PC.
- The Describe step needs the Claude Code CLI signed in on that PC
  (`claude` in a terminal → sign in once). Everything else works offline
  against Canvas only.
- An activity log of every run is kept at
  `Documents\CourseForge-PDF\activity-log.jsonl` (no tokens or
  credentials are ever written to it).
- Updating = run the newer Setup exe over the top. Uninstall = Windows
  "Add or remove programs".
