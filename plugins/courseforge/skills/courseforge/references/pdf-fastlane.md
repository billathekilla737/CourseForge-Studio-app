# PDF Fastlane — fast deterministic PDF ADA pipeline

The speed layer for PDF remediation. `Fastlane-CanvasPdfs.ps1` (gateway) +
`pdf_fastlane.py` (engine) fix the mechanical majority of a course's PDF
accessibility findings in seconds, in parallel, with **no model round-trips**;
whatever needs judgment or fails lands in a structured **fallback queue** that
the model works through afterward. Measured on a designer workstation: 43 blueprint PDFs ~19s wall -> 43/43
PDF/UA-1 COMPLIANT; 220 mixed math PDFs ~66s wall -> all 220 fixed+verified,
142/220 fully PDF/UA-1 compliant (residual = producer-tree structural damage
and non-embedded fonts, censused per rule in validation.json).

## The standard (what "compliant" means here)

The scoreboard is **PDF/UA-1 (ISO 14289-1)** validated by **veraPDF** — the
standard Section 508/WCAG guidance points at for PDF, not a course-scanner
heuristic. `validate` runs veraPDF over every fixed file and writes a per-rule
failure census (`validation.json`). A custom veraPDF profile XML (e.g.
**WTPDF 1.0 Accessibility**) can be swapped in with `--profile`. Course
scanners (UDOIT/Ally) check a small subset of this; passing them is necessary,
not sufficient — always read the validate census before declaring victory.

**Honest scope.** The fast path produces *real but basic* structure: P/H1 text
elements in content-stream order, Figure elements with alt, everything else
artifact-marked, OCR text layers, title/language/DisplayDocTitle metadata. It
does not do full semantic PDF/UA (tables, lists, reading-order reflow, font
embedding). Residual veraPDF failures it cannot fix (e.g. non-embedded fonts
in the source file) are reported, never hidden.

## Workflow

```powershell
# 1. enumerate + download (originals kept as original.pdf = the backup)
scripts\Fastlane-CanvasPdfs.ps1 -Action List  -CourseId <id>
scripts\Fastlane-CanvasPdfs.ps1 -Action Fetch -CourseId <id> -WorkDir .\pdf-fastlane

# 2. the fast pass (parallel; incremental - finished files are skipped;
#    -Force redoes everything). Exit 2 = queue has errors.
scripts\Fastlane-CanvasPdfs.ps1 -Action Process -CourseId <id> -WorkDir .\pdf-fastlane

# 3. alt text for non-trivial images (the ONE model-speed step, batched +
#    deduped by image hash so a logo used 40x costs one decision)
python scripts\pdf_fastlane.py figures --workdir .\pdf-fastlane
#    -> model VIEWS each png in alt-todo.json, writes alt.json {hash: "alt"}
python scripts\pdf_fastlane.py apply-alt --workdir .\pdf-fastlane --alt alt.json

# 4. validate against the real standard
python scripts\pdf_fastlane.py validate --workdir .\pdf-fastlane            # PDF/UA-1
python scripts\pdf_fastlane.py validate --workdir .\pdf-fastlane --profile WTPDF-1-0-Accessibility.xml

# 5. upload fixed files over the originals (links keep working).
#    DRY-RUN by default; -Apply to write. Unverified files never upload.
scripts\Fastlane-CanvasPdfs.ps1 -Action Push -CourseId <id> -WorkDir .\pdf-fastlane -Apply
```

## What the engine does per file (pdf_fastlane.py process)

1. **classify** — scanned-image / text-untagged / tagged / encrypted / signed.
2. **refuse fast** (straight to queue, file untouched): encrypted, signed,
   existing structure MCIDs in content streams. Files that ALREADY have a tag
   tree (Word exports; or a tree whose producer forgot MarkInfo) take a
   **metadata-only light lane** instead: title/lang/DisplayDocTitle, Marked
   flag, Tabs, annotation alt, missing Figure alt, font/OC fixes — the tree
   itself is honored, never rebuilt or audited.
3. **OCR** (only pages with no text layer): render 300dpi → tesseract TSV →
   invisible text (render mode 3) drawn at word boxes. Original pixels
   untouched. Words below confidence 40 dropped; file flagged for review when
   mean confidence < 55.
4. **tag** — BT..ET text blocks wrapped in /P (or /H1 by font-size heuristic)
   marked content with MCIDs; image XObjects ≥ trivial size wrapped as
   /Figure with placeholder alt (fig PNG extracted + hashed for the alt pass);
   tiny/extreme-aspect images are decorative → artifact; **everything else
   artifact-marked** (PDF/UA 7.1: content is tagged or artifact). CAD /OC
   layer marked-content is preserved and nested around correctly; blocks that
   straddle an /OC boundary are left unwrapped and counted.
5. **metadata** — docinfo + XMP dc:title (from display_name, BOM-safe),
   /Lang en-US, ViewerPreferences DisplayDocTitle.
6. **verify (independent libs)** — pypdf: page count, StructTreeRoot+Marked,
   ≥98% of original text tokens preserved; fitz: first-2-pages render
   ≥99% pixel-identical. Verify failure deletes the output and queues the
   file — an unverified fixed.pdf never exists on disk.

## The fallback queue protocol (model side)

`Process` writes `queue.json`. Each entry: file, severity (`error` = not
fixed / `review` = fixed+verified but low confidence), reason, notes, hint.
The model then:
1. Fixes the FILE: rerun `pdf_fastlane.py process` on a corrected input, or
   use the other pdf tools (`pdf_text_tool.py`, manual pikepdf surgery).
2. When one reason repeats across files, fixes the PROGRAM: patch
   `pdf_fastlane.py`, run `python pdf_fastlane.py selftest` until PASS
   (fixtures cover text/scan/mixed/OC-layer/encrypted lanes — ADD a fixture
   reproducing the new failure class first), then re-run `-Action Process`
   (incremental: only unfinished files rerun).
This loop found and fixed a real one on day 1: CAD exports carry /OC layer
BDC/EMC with no MCIDs — v1 wrongly refused all 39 such files.

## Reading a validate census correctly

`validation.json` counts **occurrences**, not files - one file with a broken
table produced 823 hits of 7.1-3. Always re-aggregate **files per rule**, and
split by lane (`metadata-only` in a result's actions = producer tree kept)
before deciding what to fix. The two questions that matter: is this failure in
a tree WE built (our bug) or one we honored (source defect), and is the fix
mechanical or semantic?

Final math-corpus numbers: **159/220 PDF/UA-1 compliant** (from 100 at first
measure). The residual is producer-tree structural damage (tables with no TH,
content neither tagged nor artifact) plus files whose font embedding failed
verify - both model/manual territory.

## Font embedding (in scope, fail-soft)

`embed_missing_fonts()` attaches the LOCAL Windows font file to unembedded
simple fonts (Arial/Times/Tahoma/Courier/Symbol + Base-14 aliases via
FONT_MAP) - full TTF, no subsetting, so zero glyph-coverage risk; existing
FontDescriptors just gain /FontFile2, bare Type1 references (Word's /Symbol)
get a synthesized descriptor + widths from fontTools metrics and become
TrueType. Safety is the verify gate, extended two ways:
- **allow_font_drift**: embedding re-rasterizes glyphs, so up to 5% pixel
  drift passes IF word GEOMETRY is stable (same words, p95 position shift
  <= 2pt) - re-rasterization moves pixels, corruption moves words.
- **fail-soft retry**: if the embedded build still fails verify (Symbol
  conversions can change text extraction - 20/220 math files did), the file
  is rebuilt WITHOUT fonts and ships as the conservative fix, with the
  residual noted. A verified-partial fix always beats a queued failure.
Embedding gained +9 compliant files net on the math corpus (150 -> 159).
Fonts NOT on the machine stay residual: that really is a source re-export.

## Gotchas (paid for)

- **Overwrite-upload issues NEW file ids.** `on_duplicate=overwrite` keeps
  the name+folder but the replacement gets a fresh id; the old id is chained
  (`GET /files/<old>` resolves to the replacement), so in-course links keep
  working - verified live after the 745063 push. Consequences: post-push
  verification must pair by (display_name, folder_id), NOT by the workdir's
  old id; and bare file URLs recorded outside Canvas redirect rather than
  resolve directly.

- **Empty `/Alt` is not missing `/Alt`.** Word writes `/Alt ""` on figures
  whose alt box the author left blank. A `get("/Alt") is None` test skips
  them and veraPDF then fails 7.3-1 - this cost 17 files on the math corpus.
  Use `_alt_missing()` (empty/whitespace counts as missing; `/ActualText`
  also satisfies). And note a placeholder only clears the RULE: an empty alt
  means nobody ever described the image, so those figures are queued for the
  real model alt pass rather than quietly "fixed".
- **veraPDF's 7.3-1 also fires on `TD`**, not just `Figure` - a table cell
  containing only an image needs its own alternative description. That is a
  semantic call (which alt for a graph inside a cell); leave it to the queue.
- **Transient/durable write locks are normal on a workstation.** Defender,
  OneDrive, the search indexer, and especially **an open Acrobat window** hold
  a freshly written PDF. `save_pdf()` writes `.part` then atomically replaces,
  retrying with backoff; a durable holder still surfaces as one actionable
  queue entry naming the likely culprit. `WinError 32` = sharing violation,
  `WinError 5` on the replace = a handle held without delete-share (check for
  an open viewer before assuming a permissions problem).
- **Killing a batch orphans its pool workers.** `TaskStop` on a running
  `-Action Process` leaves the `ProcessPoolExecutor` children alive (28 seen
  after several kills); they keep handles and CPU. Sweep them with
  `Get-CimInstance Win32_Process -Filter "Name='python3.12.exe'"` filtered on
  a `multiprocessing` command line before re-running.

- **PS 5.1 pipelines deadlock the batch.** Never pipe `-Action Process`
  output through `Measure-Command`/`Tee-Object`/`Select-Object -First` — the
  python multiprocessing pool's inherited handles hang the pipeline. Run it
  plainly (or background it and read the log).
- **PS 5.1 `Set-Content -Encoding UTF8` writes a BOM** — every json the
  gateway writes must be read with `utf-8-sig` on the python side, or titles
  silently fall back to "original".
- "Multiple definitions ... /PageMode" warnings during processing are pikepdf
  repairing malformed source dictionaries — expected on CAD exports, harmless.
- veraPDF needs Java; engine looks for `verapdf.bat` on PATH,
  `C:\Program Files\veraPDF`, `~\verapdf`, `~\tools\verapdf`, or `VERAPDF_BAT`;
  java resolves from PATH or a portable JRE under `~\tools\jdk-*` (JAVACMD).
  The WTPDF 1.0 Accessibility profile ships in the skill's `assets\` dir.
- Tesseract found via PATH, `TESSERACT_EXE`, or the UB-Mannheim default dir.
- WTPDF 1.0 targets PDF 2.0-generation tagging; on PDF 1.x course files
  expect version-related failures that are NOT fixable without rewriting the
  file as PDF 2.0 — prefer PDF/UA-1 for legacy files and say so in reports.
