# CourseForge PDF Fixer — install & first use

*For the person setting up the PCs, and for the instructors using it.*
*Current version: 1.1.8*

## Installing (per PC, ~2 minutes)

1. Copy `CourseForge-PDF-Fixer-Setup-1.1.8.exe` to the PC (USB stick or a
   shared drive is fine — no internet needed to install).
2. Double-click it and click Next, Next, Finish. **No admin password is
   needed** — it installs into the user's own profile, with a Start Menu entry
   and desktop icon.
3. If Windows shows **"Windows protected your PC"**, stop and check with IT
   rather than clicking through: that warning means the copy you have was
   downloaded by a browser and is not signed. A build signed with the
   college's certificate, or one IT pushes with Intune/SCCM, does not show it.
   Check the file's SHA-256 against the `.sha256` file published with the
   release if in doubt.

That's the whole install. Python, Tesseract, Java and veraPDF are all inside
the app — nothing else to download.

## First use (what to tell the instructor)

Open **CourseForge PDF Fixer** from the Start Menu or desktop. The window has
a course picker across the top, six buttons below it, and a log pane that
shows what is happening.

1. **Connect a course…** (top right) — paste the course web address from the
   browser, then a Canvas access token (Canvas → Account → Settings → New
   Access Token). One token covers every course on that Canvas site; it is
   stored encrypted, for that Windows account on that PC only. **Sign out of
   Canvas** (bottom of the window) removes it from the PC again — do that on a
   computer you are leaving. Give the token an expiry date when you create it.
2. **Back up & Fix PDFs** — downloads the course's PDFs and repairs them on
   this computer. **Nothing in Canvas changes during this step.**
   Originals are kept. Takes seconds to a couple of minutes.
3. **Describe images (Claude)** — writes real alt-text descriptions for the
   pictures inside the PDFs, using the instructor's own Claude account. First
   use opens a sign-in window (needs a Claude Max/Pro account signed in to
   Claude Code on that PC). Optional, but do it before uploading.
4. **Upload to Canvas** — replaces the course files in place after a
   confirmation. Course links keep working; untouched copies stay on the
   computer.
5. **Prove compliance** — runs the PDF/UA-1 standards report (the real ISO
   standard, not just a scanner score).
6. **Back up only** — downloads the originals and changes nothing else.
7. **ROLL BACK originals** (red) — puts the original PDFs back into Canvas,
   undoing an upload.

`Open work folder` at the bottom opens the folder holding this course's
originals, fixed copies and reports.

## The four things the app will tell you it cannot do

The app is deliberately honest about its limits. Read these when they appear;
each one means a person still has work to do.

- **"N file(s) need a person to look at them"** — password-protected, signed,
  or otherwise unusual PDFs were skipped, never damaged. Send the named
  `queue.json` to your instructional designer.
- **"N image(s) currently hold a SAFE PLACEHOLDER description"** — run the
  Describe step, or send the folder to your designer for that pass.
- **"N figure(s) CANNOT be described automatically"** — no picture of those
  figures could be produced (usually a tag tree that points at nothing). They
  will *pass* a compliance scan while still being useless to a screen reader,
  so somebody has to write those by hand. They are named in
  `alt-summary.json`.
- **"N figure(s) already have alt text that is only a filename"** — e.g.
  `image0.jpeg` or `Picture 3`. That passes a compliance scan and describes
  nothing. The app leaves the author's own words alone on purpose; these are
  worth a human pass. Also named in `alt-summary.json`.

A green PDF/UA-1 report plus any of the last three means "standards-compliant,
not yet genuinely accessible". Both facts are true and the app reports both.

## Notes for the deployer

- **One named Windows account per person.** Every protection in the app is
  per Windows account: the saved Canvas token only decrypts for the account
  that saved it. On a shared or generic login, whoever sits down next inherits
  the previous person's Canvas access. Do not deploy to shared logins.
- Per-user install: each Windows account that will use it runs the installer
  once, or IT pushes it per user with Intune/SCCM (a pushed install also skips
  the SmartScreen prompt entirely).
- **Signing.** Build with `Build-PdfFixer.ps1 -SignThumbprint <cert>` (an
  internal-CA or purchased code-signing certificate) or `-SignCommand` (Azure
  Trusted Signing). Unsigned builds work but SmartScreen warns on a
  browser-downloaded copy, and AppLocker/WDAC publisher rules cannot allow them.
  Every build writes a `.sha256` next to the installer; publish it.
- **Claude Code** (for the Describe step) is a separate, per-user Anthropic
  package. Have IT install it on the image rather than telling instructors to
  paste an install command into PowerShell. Everything else works without it.
- Uninstalling the app also deletes the saved Canvas token from
  `%LOCALAPPDATA%\CourseForge-PDF`. Course folders under Documents are kept.
- Working files live in `Documents\CourseForge-PDF\<course id>\`.
  If that Documents folder is redirected into OneDrive, the app says so in the
  log on startup: everything still works, but every PDF is synced to the cloud
  as well, and sync can briefly lock a file.
- The Canvas token is **not** kept with the working files. It is stored
  DPAPI-encrypted under `%LOCALAPPDATA%\CourseForge-PDF\`, which is never
  roamed or synced, and only unlocks for that Windows account on that PC. A
  token saved by version 1.1.7 or earlier is moved there automatically on
  first use. The token is only ever sent to the Canvas site it was saved for,
  over https; a course address pasted as `http://` is upgraded, and a redirect
  to any other host is refused rather than followed with the token attached.
- The Describe step needs the Claude Code CLI signed in on that PC
  (`claude` in a terminal → sign in once). Everything else works offline
  against Canvas only.
- An activity log of every run is kept at
  `Documents\CourseForge-PDF\activity-log.jsonl` (no tokens or credentials are
  ever written to it). If the app ever fails to start, it also writes
  `startup-error.txt` there and shows a dialog.
- Updating = run the newer Setup exe over the top. Uninstall = Windows
  "Add or remove programs".

## Building the installer (for whoever maintains it)

From `installer\`, on a machine with Python 3.12, the packages in
`requirements-build.txt`, and Inno Setup 6:

```powershell
python -m pip install -r requirements-build.txt
.\Build-PdfFixer.ps1 -ToolsFrom <previous build folder>   # first time: where tesseract\ verapdf\ jre\ live
.\Build-PdfFixer.ps1 -SignThumbprint <certificate sha1>   # a signed release
```

The script runs the regression tests and the engine selftest (which must print
`SELFTEST PASS`), freezes the app with PyInstaller from the **checked-in**
source (it refuses uncommitted changes unless `-AllowDirty`), verifies every
bundled Tesseract / veraPDF / JRE file against the SHA-256 digests in
`bundled-tools.json`, smoke-tests the frozen exe, signs it when a certificate
is given, compiles the installer, and writes its SHA-256 beside it. Those
third-party tools are downloaded once from their publishers (URLs in
`bundled-tools.json`); after a deliberate upgrade, run with
`-RecordToolHashes`, check the new digests against the publisher's download,
and commit the file.
