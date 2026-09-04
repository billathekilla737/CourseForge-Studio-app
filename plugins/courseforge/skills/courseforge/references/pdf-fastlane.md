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
   /Figure with placeholder alt (the picture for the alt pass is produced
   later, at `figures` time, from the finished fixed.pdf - see the alt
   pipeline note below);
   tiny/extreme-aspect images are decorative → artifact; **everything else
   artifact-marked** (PDF/UA 7.1: content is tagged or artifact). CAD /OC
   layer marked-content is preserved and nested around correctly; blocks that
   straddle an /OC boundary are left unwrapped and counted.
5. **metadata** — docinfo + XMP dc:title (from display_name, BOM-safe),
   /Lang en-US, ViewerPreferences DisplayDocTitle.
6. **verify (independent libs)** — pypdf: page count, StructTreeRoot+Marked,
   ≥98% of original text tokens preserved; fitz: first-VERIFY_PAGES (4)
   render ≥99% pixel-identical. Verify failure deletes the output and queues the
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

The 2026-08-25 repair round (v1.1.6 engine) drove two full course corpora to
**100%**: MAT-1313 735346 218/218 (from 184/216) and stats 735867 197/197.
The passes that got there, in the order the errors fell:
- **7.21.7 gapped ToUnicode**: `augment_cid_tounicode` now also fills simple
  (TrueType/Type1) fonts with an EXISTING map — code width auto-detected
  (1-byte `<XX>` vs 2-byte `<XXXX>` srcs), and bf dst parsing must allow
  multi-char/surrogate destinations (`<0020 00a0>`, `<d835 dc5b>`).
- **verify text gate**: compares alnum-only token projections — glyph-name
  junk and unmapped-glyph garbage change legitimately when maps are repaired.
- **7.2-29**: `normalize_langs` strips non-printable bytes (`en-us\x00`).
- **7.2-42 merged bands**: a row with ONE content-bearing cell spans the
  modal table width via ColSpan; padding it with empty TDs equalizes struct
  width but still fails veraPDF's geometric check. "Content-bearing" must be
  decided by whether the MCID region PAINTS anything (Word gives empty filler
  cells real MCIDs).
- **7.1-3 trio**: `fix_mc_nesting` drops struct-orphaned non-MCID wrappers
  (`/NonStruct <<>>`, `/Div <<>>` — /OC preserved); `artifact_form_content`
  converts orphan BDC regions inside Form XObjects to /Artifact and wraps
  unmarked runs; `fix_struct_parentage` rewires MISSING StructElem /P
  pointers (OneNote omits them and veraPDF then treats every owned MCID as
  untagged — 737 occurrences from 18 missing /P).
- **7.21.8 .notdef**: `strip_notdef_glyphs` removes zero code units from show
  strings (pages + forms), both lanes.
- **Symbol/ZapfDingbats embedding**: three cases. (a) bare base-14 with
  built-in encoding → attach PyMuPDF's bundled CFF (`fitz.Font("symb"/"zadb")
  .buffer`) as FontFile3/Type1C and rebuild /Widths from the CFF charstrings
  (draw a NullPen — `.width` is set as a side effect; producers ship zeroed
  Widths). (b) /Differences with all-AGL names Arial covers → embed Arial
  nonsymbolic, keep encoding, widths via AGL. (c) /Differences with Symbol
  piece names (parenleftex... = Adobe corporate PUA, no live font) →
  `_synth_diff_ttf` subsets Segoe UI Symbol into a symbolic TTF with a (3,0)
  cmap at F000+code (fontTools `cmap_format_4(4)`), plus a hand-built
  ToUnicode.

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

## Standalone installer (non-technical users)

`scripts/courseforge_pdf.py` is a wizard front-end (numbered menu: connect
course / check & fix / upload with confirmation / prove compliance) over the
same engine, with a stdlib-only Canvas client - built for instructors who
have never seen a terminal. It freezes with PyInstaller and ships with
bundled Tesseract (eng), veraPDF, and a portable JRE next to the exe
(`wire_bundled_tools()` points the engine's env-var discovery at them), so
an installed PC needs NO Python, pip, or PowerShell. Packaging lives in the
project `installer/` dir: PyInstaller onedir -> bundle copy-in -> Inno Setup
per-user installer (no admin rights needed; `PrivilegesRequired=lowest`).
Frozen entry point MUST call `multiprocessing.freeze_support()` first or the
batch pool forkbombs the app. The wizard leaves alt-text as safe placeholders
and tells the user to send the workdir to a designer for the description
pass - the model step deliberately stays out of the standalone app.
Mac users: no fork - the engine is cross-platform; `brew install tesseract
verapdf` + `pipx install` covers it (font paths are the one Windows-ism to
abstract before advertising Mac support).

## Performance notes (profiled 2026-08-24)

- Per-stage `timings` in every result.json are the profiler - aggregate them
  before optimizing. First census: **verify was 67% of ALL compute** because
  the render compare counted matching bytes in a pure-Python loop; replaced
  with byte-equality fast path + XOR count at C speed, and the redundant
  second verify per lane was removed (metadata lane verified every file
  twice). OCR (~25s/course) and tag are the honest remainder.
- **`-Force` regenerates fixed.pdf WITHOUT previously applied alt text** -
  always re-run `apply-alt` after a forced rebatch, or the next Push
  downgrades Canvas.
- On a high-latency link (measured: 218ms median RTT, 6.6 Mbps), transfers
  dominate wall time: Fetch now uses one `curl --parallel` run over the
  pre-signed file urls, and Push runs the multipart uploads 6 at a time
  (slot requests stay sequential). A failed parallel upload prints FAILED
  and is retried by re-running Push - slots are single-use.

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

## The alt pipeline: one enumeration, one ordinal

Three separate index bases used to disagree about what a figure's index meant
(alt-LESS figures in one place, ALL figures in another, images-on-the-page in a
third). The result, reproduced on a two-figure Word export: the description
landed on figure 1, **destroying the author's real alt text**, while figure 2
kept the placeholder. Rules now:

- `walk_figures(pdf)` is THE enumeration. `alt_existing_figures`,
  `collect_alt_todo` and `apply_alt` all use it. Do not hand-roll another
  tree walk.
- An ordinal is only ever compared against the **same file** it was read from.
  `collect_alt_todo` and `apply_alt` both read `fixed.pdf`, not `result.json`.
- `apply_alt` writes a figure **only if it still holds `PLACEHOLDER_ALT`**
  (or nothing). Authored text - the author's, or an earlier run's - is never
  overwritten, and a skip is reported.
- `PLACEHOLDER_ALT` is the marker that means "undescribed". Both lanes write
  exactly that string, and the alt pass finds its work by looking for it.
  Changing it strands figures in already-fixed PDFs.

### Finding the picture a describer looks at

A `/Figure` points at marked content, not at an image, so pairing takes four
tiers in descending fidelity. `kind` in `alt-todo.json` says which was used:

| kind | how | when |
|---|---|---|
| `region` | the figure's own MCIDs -> content-stream bbox -> clip render | preferred; the only tier that works for VECTOR art |
| `image` | a raster image XObject paired positionally, original pixels | figure has no usable MCID bbox |
| `drawing` | clustered vector bounding box, paired positionally | no raster to pair |
| `page` | the whole page | ONLY when it is the sole figure on that page |

The MCID tier matters more than it sounds. `page.get_images()` returns nothing
for a Word shape/chart/SmartArt, so positional-against-rasters produced
`png: None` for **16 of 21 figures** on the live math course - and those
shipped a meaningless placeholder under a green PDF/UA report. MCID bboxes fix
all 16. Note `_pdf_rect_to_page` applies `transformation_matrix` **then**
`rotation_matrix`: a `/Rotate 90` page (landscape handouts) lands every clip
off-page without the second one.

### Reporting what cannot be fixed

`alt-summary.json` carries the honest counts, and the wizard prints them:

- `no_picture_available` - figures with no renderable picture (usually a
  `/Figure` whose `/K` is empty, so nothing says where it is). They keep the
  placeholder and are **named**; a person has to write those.
- `useless_existing_alt` - alt that is only a filename (`image0.jpeg`) or
  `Picture 3`. Passes 7.3-1, describes nothing. Reported and deliberately
  **left alone** - authored content is not ours to overwrite.

A green PDF/UA-1 report plus either of those means "standards-compliant, not
yet genuinely accessible". Report both; never let the first hide the second.
