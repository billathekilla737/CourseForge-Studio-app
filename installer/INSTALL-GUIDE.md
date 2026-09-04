# CourseForge PDF Fixer — install & first use

*For the person setting up the PCs, and for the instructors using it.*
*Current version: 1.1.8*

## Installing (per PC, ~2 minutes)

1. Copy `CourseForge-PDF-Fixer-Setup-1.1.8.exe` to the PC (USB stick or a
   shared drive is fine — no internet needed to install).
2. Double-click it. If Windows SmartScreen shows "Windows protected your PC",
   click **More info → Run anyway** (the installer is unsigned; see note below).
3. Click Next, Next, Finish. **No admin password is needed** — it installs
   into the user's own profile, with a Start Menu entry and desktop icon.

That's the whole install. Python, Tesseract, Java and veraPDF are all inside
the app — nothing else to download.

## First use (what to tell the instructor)

Open **CourseForge PDF Fixer** from the Start Menu or desktop. The window has
a course picker across the top, six buttons below it, and a log pane that
shows what is happening.

1. **Connect a course…** (top right) — paste the course web address from the
   browser, then a Canvas access token (Canvas → Account → Settings → New
   Access Token). One token covers every course on that Canvas site; it is
   stored encrypted, for that Windows account on that PC only.
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

- Per-user install: each Windows account that will use it runs the installer
  once. For all-users or Intune/SCCM push, ask about building the MSI variant.
- Unsigned binary: SmartScreen will warn on first run. A college code-signing
  certificate removes this — worth requesting from IT if this rolls out wider.
- Working files live in `Documents\CourseForge-PDF\<course id>\`.
  If that Documents folder is redirected into OneDrive, the app says so in the
  log on startup: everything still works, but every PDF is synced to the cloud
  as well, and sync can briefly lock a file.
- The Canvas token is **not** kept with the working files. It is stored
  DPAPI-encrypted under `%LOCALAPPDATA%\CourseForge-PDF\`, which is never
  roamed or synced, and only unlocks for that Windows account on that PC. A
  token saved by version 1.1.7 or earlier is moved there automatically on
  first use.
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

The application source lives in the CourseForge **skill**, not next to the
spec file:

```
%USERPROFILE%\.claude\skills\courseforge\scripts\
```

From `installer\`:

```
python -m PyInstaller courseforge-pdf.spec --distpath dist --workpath build
```

The spec finds the source via `CF_SCRIPTS`, then the default skill location,
then `..\scripts` — set `CF_SCRIPTS` if yours is elsewhere. Then copy the
bundled `tesseract\`, `verapdf\` and `jre\` folders next to the built
`courseforge-pdf.exe` in `dist\courseforge-pdf\`, and compile
`courseforge-pdf.iss` with Inno Setup.

Before shipping a build, run the regression harness:

```
python %USERPROFILE%\.claude\skills\courseforge\scripts\pdf_fastlane.py selftest
```

It covers the whole alt-text pipeline (batch → collect → apply), the
producer-tree and OCR lanes, and the refusal paths. It must print
`SELFTEST PASS`.
