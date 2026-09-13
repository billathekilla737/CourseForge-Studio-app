"""
pdf_fastlane.py (courseforge) - FAST deterministic PDF ADA pipeline.

The speed layer for PDF remediation: everything mechanical happens here in
milliseconds-to-seconds per file, with NO model round-trips. Anything that
needs judgment, or anything this tool is not confident about, lands in a
structured fallback queue (queue.json) for the model to handle - and the
selftest below is the regression harness the model uses when it patches
THIS file after diagnosing a queue entry.

What the fast path fixes (the checks course scanners actually run):
  "Image-based file detected"   -> OCR text layer (tesseract TSV -> invisible
                                   text drawn at word coordinates, original
                                   pixels untouched)
  "File lacks tags..."          -> real (basic) tag tree: /StructTreeRoot,
                                   /MarkInfo Marked, per-block P/H1 StructElems
                                   wired to marked-content MCIDs, figures
                                   tagged with alt
  missing title / language      -> docinfo + XMP dc:title, /Lang, viewer
                                   pref DisplayDocTitle

HONEST SCOPE: the tags are REAL and standards-valid but BASIC - paragraphs,
simple headings, figures, in content-stream order. That satisfies automated
checkers and gives AT users a navigable structure, but it is not full PDF/UA
semantic fidelity: complex drawings still deserve human/model review (which is
what the confidence gate flags). Never claim more than this.

Hard-refuse -> fallback queue (never touched): encrypted, digitally signed,
already-tagged (don't clobber someone's real work), existing marked-content
(can't safely interleave), unbalanced text blocks, or any verify failure.

Commands:
  scan   <pdf...> --json out.json          fast classification (pikepdf+fitz)
  process <pdf> --out fixed.pdf [--report r.json] [--title "..."]
  batch  --workdir DIR [--jobs N]          process every DIR/<id>/original.pdf
                                           -> fixed.pdf + result.json + top-level
                                           summary.json + queue.json
  verify <original.pdf> <fixed.pdf>        independent re-check (pypdf + render)
  selftest                                 synthesize fixtures, run pipeline,
                                           assert - the model's regression net

Requires: pymupdf, pikepdf, pypdf; tesseract.exe for OCR pages
(PATH, TESSERACT_EXE env, or the default UB-Mannheim install dir).
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

# In a WINDOWED (no-console) exe sys.stdout is None on double-click -
# guard everything here or import itself crashes (incl. pool workers).
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# pypdf logs xref-repair chatter ("Ignoring wrong pointing object") straight
# to the console while VERIFYING malformed source files - harmless, repaired
# on read, and terrifying to a non-technical user. Errors still surface.
import logging
logging.getLogger("pypdf").setLevel(logging.ERROR)

import pymupdf as fitz  # modern import; 'fitz' alias keeps the codebase stable
import pikepdf
from pikepdf import Name, Dictionary, Array, String, Operator

# console children (tesseract/verapdf/claude) each pop a cmd window when
# the parent is a WINDOWED app - suppress it on every non-interactive call
SUBPROC_FLAGS = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW

OCR_DPI = 300
OCR_MIN_WORD_CONF = 40.0   # tesseract per-word confidence floor
OCR_LOW_MEAN_CONF = 55.0   # below this the file is flagged for review
HEADING_RATIO = 1.45       # block font >= ratio * body median -> heading
HEADING_MAX_CHARS = 90
CONFIDENCE_REVIEW = 0.60   # below -> "review" queue entry (file still fixed)
RENDER_MATCH_MIN = 0.99    # pixel-identical ratio required on smoke render
VERIFY_PAGES = 4           # pages render/geometry-checked per file (verify_pair)

# The one placeholder string both lanes write onto an undescribed Figure. It is
# ALSO the marker meaning "nothing has described this yet": the alt pipeline
# finds its work by looking for it and REFUSES to overwrite any other text, so
# a real description (the author's or a previous run's) can never be clobbered.
# Changing it strands figures inside already-fixed PDFs - migrate if you must.
PLACEHOLDER_ALT = "Graphic in this document"
DECORATIVE_ALT = "Decorative graphic"


# ---------------------------------------------------------------- utilities

def find_tesseract():
    exe = os.environ.get("TESSERACT_EXE")
    if exe and os.path.isfile(exe):
        return exe
    hit = shutil.which("tesseract")
    if hit:
        return hit
    for guess in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.isfile(guess):
            return guess
    return None


def title_from_filename(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"^\d+_", "", stem)          # strip leading fileid_ prefixes
    stem = stem.replace("_", " ").replace("-", " - ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "Course document"


def _op(instr):
    return str(instr.operator) if hasattr(instr, "operator") else None


# ---------------------------------------------------------------- scanning

def classify(path):
    """Fast triage (same classes as triage_pdf.py, faster libs)."""
    r = {"file": os.path.basename(path), "path": path}
    try:
        with pikepdf.open(path) as pdf:
            r["signed"] = _is_signed(pdf)
            root = pdf.Root
            mi = root.get("/MarkInfo")
            r["tagged"] = bool(mi is not None and bool(mi.get("/Marked", False))
                               and root.get("/StructTreeRoot") is not None)
    except pikepdf.PasswordError:
        r.update(cls="encrypted", note="password-protected")
        return r
    except Exception as e:
        r.update(cls="empty/odd", note="unreadable: %s" % e)
        return r
    doc = fitz.open(path)
    n = doc.page_count
    text_pages = img_pages = 0
    ocr_pages = []
    for i, pg in enumerate(doc):
        has_text = len(pg.get_text("text").strip()) > 20
        has_img = bool(pg.get_images(full=False))
        if has_text:
            text_pages += 1
        else:
            ocr_pages.append(i)
        if has_img:
            img_pages += 1
    doc.close()
    r.update(pages=n, text_pages=text_pages, image_pages=img_pages,
             ocr_pages=ocr_pages)
    if r["signed"]:
        r["cls"] = "signed"
    elif n and text_pages / n < 0.5 and img_pages > 0:
        r["cls"] = "scanned-image"
    elif text_pages == 0 and img_pages == 0:
        r["cls"] = "empty/odd"
    elif r["tagged"]:
        r["cls"] = "tagged"
    else:
        r["cls"] = "text-untagged"
    return r


def _has_struct_tree(path):
    try:
        with pikepdf.open(path) as pdf:
            return pdf.Root.get("/StructTreeRoot") is not None
    except Exception:
        return False


def _is_signed(pdf):
    try:
        acro = pdf.Root.get("/AcroForm")
        if acro is None:
            return False
        for f in acro.get("/Fields", []):
            if f.get("/FT") == Name("/Sig") and f.get("/V") is not None:
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------- OCR stage

def ocr_pages_inplace(doc, page_idxs, tess, notes):
    """Render each page, tesseract TSV, draw invisible words at their boxes.
    Returns (words_added, mean_confidence)."""
    total_words, confs = 0, []
    with tempfile.TemporaryDirectory() as td:
        for i in page_idxs:
            pg = doc[i]
            zoom = OCR_DPI / 72.0
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png = os.path.join(td, "p%d.png" % i)
            pix.save(png)
            cp = subprocess.run(
                [tess, png, "stdout", "--dpi", str(OCR_DPI), "-l", "eng", "tsv"],
                capture_output=True, timeout=180, creationflags=SUBPROC_FLAGS)
            if cp.returncode != 0:
                raise RuntimeError("tesseract failed on page %d: %s"
                                   % (i + 1, cp.stderr.decode("utf-8", "replace")[:300]))
            for line in cp.stdout.decode("utf-8", "replace").splitlines()[1:]:
                c = line.split("\t")
                if len(c) < 12 or c[0] != "5":       # level 5 = word
                    continue
                try:
                    conf = float(c[10])
                except ValueError:
                    continue
                word = c[11].strip()
                if conf < OCR_MIN_WORD_CONF or not word:
                    continue
                x, y, h = (float(c[6]) / zoom, float(c[7]) / zoom,
                           float(c[9]) / zoom)
                fs = max(4.0, h * 0.85)
                # render_mode 3 = invisible; text layer only, pixels untouched
                pg.insert_text(fitz.Point(x, y + h * 0.8), word,
                               fontsize=fs, fontname="helv", render_mode=3)
                total_words += 1
                confs.append(conf)
    mean_conf = statistics.fmean(confs) if confs else 0.0
    if page_idxs and not total_words:
        notes.append("OCR found no confident words on %d page(s)" % len(page_idxs))
    return total_words, mean_conf


# ------------------------------------------------------------- tagging stage

class TagAbort(Exception):
    """Structural state we refuse to tag through -> fallback queue."""


def atomic_replace(tmp, out_path, attempts=5):
    """os.replace, retrying transient Windows write locks. On a designer
    workstation Defender / OneDrive / the search indexer routinely hold a
    freshly written PDF for a second or two and a bare replace dies with
    PermissionError - observed live on the math corpus. Retrying beats
    surfacing a phantom failure to the queue.

    NOTE: out_path must not be open anywhere in this process. Windows refuses
    to replace a file pikepdf still has mapped, which is why in-place rewrites
    (apply_alt) save inside the `with` and replace after it closes."""
    last = None
    for i in range(attempts):
        try:
            os.replace(tmp, out_path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.4 * (i + 1))
        except OSError as e:
            last = e
            time.sleep(0.2 * (i + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise PermissionError(
        "could not write %s after %d attempts (file locked by another "
        "process - antivirus/OneDrive/indexer?): %s" % (out_path, attempts, last))


def save_pdf(pdf, out_path, attempts=5):
    """Save via a temp file then atomic replace. Never leaves a half-written
    PDF where a reader might pick one up."""
    tmp = out_path + ".part"
    last = None
    for i in range(attempts):
        try:
            pdf.save(tmp)
            break
        except (PermissionError, OSError) as e:
            last = e
            time.sleep(0.3 * (i + 1))
    else:
        raise PermissionError(
            "could not write %s after %d attempts (file locked by another "
            "process - antivirus/OneDrive/indexer?): %s"
            % (out_path, attempts, last))
    atomic_replace(tmp, out_path, attempts)


DECOR_MIN_DIM = 32          # image <= this on either side -> decorative
DECOR_MAX_ASPECT = 25.0     # rules/borders masquerading as images


def _image_xobjects(page_obj):
    """{name: (w, h, decorative)} for image XObjects; plus form-with-text
    names (their Do is left unwrapped - tagging inside a Form needs its own
    StructParents plumbing, which is fallback-queue territory)."""
    imgs, text_forms = {}, set()
    try:
        res = page_obj.get("/Resources")
        xo = res.get("/XObject") if res is not None else None
        if xo is None:
            return imgs, text_forms
        for k in xo.keys():
            try:
                sub = xo[k].get("/Subtype")
                if sub == Name("/Image"):
                    w = int(xo[k].get("/Width", 0))
                    h = int(xo[k].get("/Height", 0))
                    aspect = (max(w, h) / max(1, min(w, h)))
                    decorative = (min(w, h) <= DECOR_MIN_DIM
                                  or aspect >= DECOR_MAX_ASPECT)
                    imgs[str(k)] = (w, h, decorative)
                elif sub == Name("/Form"):
                    try:
                        fdata = xo[k].read_bytes()  # DECODED - the raw
                        # (compressed) bytes match nothing, hiding text forms
                    except Exception:
                        fdata = b"BT"               # unreadable: assume text
                    if b"BT" in fdata:
                        text_forms.add(str(k))
            except Exception:
                continue
        return imgs, text_forms
    except Exception:
        return imgs, text_forms


def _block_font_stats(block):
    """(max effective font size, char count, has_text) for one BT..ET slice."""
    size, eff, chars = 0.0, 0.0, 0
    for ins in block:
        op = _op(ins)
        if op == "Tf" and len(ins.operands) >= 2:
            try:
                size = float(ins.operands[1])
            except Exception:
                pass
            eff = max(eff, size)
        elif op == "Tm" and len(ins.operands) >= 6:
            try:
                d = abs(float(ins.operands[3]))
                if d:
                    eff = max(eff, size * d)
            except Exception:
                pass
        elif op in ("Tj", "'", '"'):
            try:
                chars += len(str(ins.operands[-1]))
            except Exception:
                chars += 1
        elif op == "TJ":
            try:
                for el in ins.operands[0]:
                    if isinstance(el, String) or isinstance(el, str):
                        chars += len(str(el))
            except Exception:
                chars += 1
    return eff, chars, chars > 0


def _mc_delta(ins):
    """Marked-content nesting change contributed by one instruction."""
    op = _op(ins)
    if op in ("BDC", "BMC"):
        return 1
    if op == "EMC":
        return -1
    return 0


def tag_pdf(pdf, notes):
    """Wrap text blocks and images in marked content, build the tree.
    Returns (n_text_elems, n_figures, n_headings).

    Existing BDC/EMC is common and usually harmless: CAD exports wrap layers
    in /OC (optional content) marked content, which carries no /MCID and is
    NOT structure. We only refuse when real structure MCIDs already exist -
    interleaving two MCID numberings corrupts the parent tree. A text block
    that STRADDLES an existing marked-content boundary is left unwrapped
    (wrapping it would break nesting); everything else proceeds."""
    if pdf.Root.get("/StructTreeRoot") is not None:
        raise TagAbort("StructTreeRoot already present (but file not marked "
                       "as tagged) - inspect by hand instead of clobbering")

    # pass 1: measure every text block across the doc for the heading heuristic
    all_sizes = []
    per_page_instr = []
    for page in pdf.pages:
        instrs = pikepdf.parse_content_stream(page)
        per_page_instr.append(instrs)
        depth = 0
        block = []
        for ins in instrs:
            op = _op(ins)
            if op == "BDC" and len(ins.operands) > 1:
                try:
                    if "/MCID" in ins.operands[1].keys():
                        raise TagAbort("existing structure MCIDs in content "
                                       "stream; cannot renumber safely")
                except AttributeError:
                    pass          # props by name reference, not inline dict
            if op == "BT":
                if depth:
                    raise TagAbort("nested BT without ET (malformed stream)")
                depth, block = 1, []
            elif op == "ET":
                if not depth:
                    raise TagAbort("ET without BT (malformed stream)")
                depth = 0
                eff, chars, has = _block_font_stats(block)
                if has and eff > 0:
                    all_sizes.append(eff)
            elif depth:
                block.append(ins)
        if depth:
            raise TagAbort("unterminated BT block")
    body = statistics.median(all_sizes) if all_sizes else 0.0

    doc_kids = Array()
    parent_nums = Array()
    struct_root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc_elem = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Document, P=struct_root, K=doc_kids))

    n_text = n_fig = n_head = n_art = 0
    figures = []          # [{page, index, name, width, height}] for alt work
    for pi, page in enumerate(pdf.pages):
        instrs = per_page_instr[pi]
        img_info, text_forms = _image_xobjects(page.obj)
        if text_forms:
            notes.append("page %d: Form XObject contains text (left untagged)" % (pi + 1))
        out = []
        mcid = 0
        page_elems = Array()
        pending = []      # ordinary ops awaiting an /Artifact wrap

        def flush_artifact():
            # PDF/UA 7.1: content is either real (tagged) or an artifact.
            # Wrap each pending run; runs never contain mc-delta ops, so
            # nesting with existing /OC marked content stays balanced.
            nonlocal n_art
            if not pending:
                return
            out.append(([Name("/Artifact")], Operator("BMC")))
            out.extend(pending)
            out.append(([], Operator("EMC")))
            n_art += 1
            del pending[:]

        def open_mc(tag):
            out.append(([Name(tag), Dictionary(MCID=mcid)], Operator("BDC")))

        def add_elem(stype, alt=None):
            nonlocal mcid
            d = Dictionary(Type=Name.StructElem, S=Name(stype), P=doc_elem,
                           Pg=page.obj, K=mcid)
            if alt:
                d.Alt = String(alt)
            ref = pdf.make_indirect(d)
            doc_kids.append(ref)
            page_elems.append(ref)
            mcid += 1

        skipped_straddle = 0
        i, n = 0, len(instrs)
        while i < n:
            ins = instrs[i]
            op = _op(ins)
            if op == "BT":
                j = i + 1
                block = []
                while j < n and _op(instrs[j]) != "ET":
                    block.append(instrs[j])
                    j += 1
                eff, chars, has = _block_font_stats(block)
                straddles = sum(_mc_delta(b) for b in block) != 0
                # a block whose internal BDC/EMC do not balance straddles an
                # existing marked-content region - wrapping it breaks nesting
                if has and straddles:
                    skipped_straddle += 1
                    has = False
                if has:
                    flush_artifact()
                    heading = (body and eff >= HEADING_RATIO * body
                               and chars <= HEADING_MAX_CHARS)
                    tag = "/H1" if heading else "/P"
                    open_mc(tag)
                    out.append(ins)
                    out.extend(block)
                    out.append(instrs[j])          # the ET
                    out.append(([], Operator("EMC")))
                    add_elem(tag)
                    n_text += 1
                    n_head += 1 if heading else 0
                elif straddles:                     # leave unwrapped entirely
                    flush_artifact()
                    out.append(ins)
                    out.extend(block)
                    out.append(instrs[j])
                else:                               # positioning-only block
                    pending.append(ins)
                    pending.extend(block)
                    pending.append(instrs[j])
                i = j + 1
                continue
            if (op == "Do" and len(ins.operands) == 1
                    and str(ins.operands[0]) in img_info):
                name = str(ins.operands[0])
                w, h, decorative = img_info[name]
                if decorative:
                    pending.append(ins)             # artifact, not Figure
                else:
                    flush_artifact()
                    open_mc("/Figure")
                    out.append(ins)
                    out.append(([], Operator("EMC")))
                    add_elem("/Figure", alt=PLACEHOLDER_ALT)
                    figures.append({"page": pi, "index": n_fig, "name": name,
                                    "width": w, "height": h})
                    n_fig += 1
                i += 1
                continue
            if _mc_delta(ins) != 0 or (op == "Do" and len(ins.operands) == 1
                                       and str(ins.operands[0]) in text_forms):
                # existing marked-content boundary, or a text-bearing form:
                # never absorb into an artifact run
                flush_artifact()
                out.append(ins)
                i += 1
                continue
            pending.append(ins)
            i += 1
        flush_artifact()

        if skipped_straddle:
            notes.append("page %d: %d text block(s) straddle existing marked "
                         "content and were left untagged" % (pi + 1, skipped_straddle))

        page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
        if "/StructParents" not in page.obj:
            page.obj.StructParents = pi
        parent_nums.append(pi)
        parent_nums.append(pdf.make_indirect(page_elems))

    n_annot, next_key = ua_harden(pdf, doc_elem, doc_kids, parent_nums,
                                  next_key=len(pdf.pages), notes=notes)
    struct_root.K = doc_elem
    struct_root.ParentTree = pdf.make_indirect(Dictionary(Nums=parent_nums))
    struct_root.ParentTreeNextKey = next_key
    pdf.Root.StructTreeRoot = struct_root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    return n_text, n_fig, n_head, n_art, n_annot, figures


ANNOT_SKIP = {"/Widget", "/PrinterMark", "/Popup"}


def _annot_hidden(a):
    try:
        return bool(int(a.get("/F", 0)) & 2)
    except Exception:
        return False


def _annot_contents(a):
    sub = str(a.get("/Subtype"))
    if a.get("/Contents"):
        return None                       # already described
    if sub == "/Link":
        act = a.get("/A")
        uri = str(act.get("/URI")) if act is not None and act.get("/URI") else None
        return "Link to %s" % uri if uri else "Link within this document"
    if sub == "/Square":
        return "Drawing markup rectangle"
    return "%s annotation" % sub.lstrip("/")


def ua_harden(pdf, doc_elem=None, doc_kids=None, parent_nums=None,
              next_key=0, notes=None):
    """Deterministic PDF/UA-1 conformance work beyond basic tagging - every
    fix here is content-neutral (census-driven from the first veraPDF run):
      7.18.1 annotations into the structure tree (OBJR) with /Contents alt
      7.18.3 /Tabs /S on pages with annotations
      7.18.5 Link annots under /Link structure elements
      7.10   optional-content configs get a /Name
      7.21   embedded CIDFontType2 get an explicit /CIDToGIDMap
      5      pdfuaid:part=1 XMP identification
    Annotation structuring needs OUR tree (doc_elem); on files keeping a
    producer's tree it is skipped and reported instead of grafting onto a
    foreign ParentTree."""
    notes = notes if notes is not None else []
    n_annot = 0
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None or len(annots) == 0:
            continue
        page.obj.Tabs = Name("/S")
        if doc_elem is None:
            # producer tree kept: no OBJR grafting, but /Contents alt is a
            # plain dictionary key - set it anyway (7.18.1-2 / 7.18.5-2)
            for a in annots:
                sub = str(a.get("/Subtype"))
                if sub in ANNOT_SKIP or _annot_hidden(a):
                    continue
                alt = _annot_contents(a)
                if alt:
                    a.Contents = String(alt)
            notes.append("annotations present but producer tree kept - "
                         "Contents alt set, OBJR tagging skipped (residual 7.18.1-1)")
            continue
        for a in annots:
            sub = str(a.get("/Subtype"))
            if sub in ANNOT_SKIP or _annot_hidden(a):
                continue
            stype = "/Link" if sub == "/Link" else "/Annot"
            elem = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name(stype), P=doc_elem, Pg=page.obj,
                K=Dictionary(Type=Name.OBJR, Obj=a)))
            alt = _annot_contents(a)
            if alt:
                a.Contents = String(alt)
            a.StructParent = next_key
            parent_nums.append(next_key)
            parent_nums.append(elem)
            doc_kids.append(elem)
            next_key += 1
            n_annot += 1

    # optional-content configurations need names (7.10)
    ocp = pdf.Root.get("/OCProperties")
    if ocp is not None:
        d = ocp.get("/D")
        if d is not None and d.get("/Name") is None:
            d.Name = String("Default configuration")
        for cfg in (ocp.get("/Configs") or []):
            if cfg.get("/Name") is None:
                cfg.Name = String("Configuration")

    # explicit CIDToGIDMap on embedded Type2 CID fonts (7.21.3.2; /Identity
    # is the spec default, making it explicit is rendering-neutral). Walk
    # RESOURCES, not pdf.objects: descendant font dicts are often DIRECT
    # objects inside the DescendantFonts array, which pdf.objects never
    # yields - the first census run proved this the hard way.
    def _fix_fonts(res):
        if res is None:
            return
        fonts = res.get("/Font")
        if fonts is None:
            return
        for k in fonts.keys():
            try:
                f = fonts[k]
                if f.get("/Subtype") != Name("/Type0"):
                    continue
                for df in (f.get("/DescendantFonts") or []):
                    if (df.get("/Subtype") == Name("/CIDFontType2")
                            and df.get("/CIDToGIDMap") is None):
                        df.CIDToGIDMap = Name("/Identity")
            except Exception:
                continue

    for page in pdf.pages:
        res = page.obj.get("/Resources")
        _fix_fonts(res)
        xo = res.get("/XObject") if res is not None else None
        if xo is not None:
            for k in xo.keys():
                try:
                    if xo[k].get("/Subtype") == Name("/Form"):
                        _fix_fonts(xo[k].get("/Resources"))
                except Exception:
                    continue
    return n_annot, next_key


def _alt_missing(node):
    """PDF/UA 7.3-1 needs a NON-EMPTY alternative. Word writes /Alt "" when
    the author left the alt box blank, and an empty string is not None - the
    first math-corpus run failed 17 files on exactly this. Treat empty or
    whitespace-only /Alt as missing; /ActualText also satisfies the rule."""
    for key in ("/Alt", "/ActualText"):
        v = node.get(key)
        if v is not None and str(v).strip():
            return False
    return True




def _rolemap(pdf):
    """StructTreeRoot /RoleMap as {customRole: standardRole} strings."""
    rm = {}
    try:
        r = pdf.Root.StructTreeRoot.get("/RoleMap")
        if r is not None:
            for k in r.keys():
                rm[str(k)] = str(r[k])
    except Exception:
        pass
    return rm


def _resolved_role(node, rm):
    """Effective structure role after following the RoleMap chain - Word
    tags figures as /InlineShape mapped to /Figure; raw /S comparison
    misses every role-mapped element."""
    try:
        v = node.get("/S")
    except Exception:
        return None
    if v is None:
        return None
    v = str(v)
    seen = set()
    while v in rm and v not in seen:
        seen.add(v)
        v = rm[v]
    return v


def _page_index_map(pdf):
    """{page objgen: page number} so a StructElem's /Pg resolves to an index."""
    out = {}
    for i, page in enumerate(pdf.pages):
        try:
            out[tuple(page.obj.objgen)] = i
        except Exception:
            pass
    return out


def walk_figures(pdf):
    """EVERY /Figure StructElem in tree order, as [(ordinal, node, page)].

    THE single enumeration used by alt_existing_figures, collect_alt_todo and
    apply_alt. Three hand-rolled copies of this walk used to disagree about
    what an index counted (alt-LESS figures here, ALL figures there), so a
    model-written description landed on the wrong figure and overwrote the
    author's real alt text. One walk, one ordinal, no handoff.
    """
    rm = _rolemap(pdf)
    pages = _page_index_map(pdf)
    found, seen = [], set()

    def visit(node, depth):
        if depth > 60 or node is None:
            return
        try:
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if isinstance(node, (Array, list)):
                for kid in node:
                    visit(kid, depth + 1)
                return
            if not hasattr(node, "get"):
                return
            if _resolved_role(node, rm) == "/Figure":
                pi = None
                pg = node.get("/Pg")
                if pg is not None:
                    try:
                        pi = pages.get(tuple(pg.objgen))
                    except Exception:
                        pi = None
                found.append((len(found), node, pi))
            k = node.get("/K")
            if k is not None and not isinstance(k, int):
                visit(k, depth + 1)
        except Exception:
            return

    try:
        root = pdf.Root.get("/StructTreeRoot")
        if root is not None:
            visit(root.get("/K"), 0)
    except Exception:
        pass
    return found


def _alt_text(node):
    """Current /Alt as a plain string ('' when absent)."""
    try:
        v = node.get("/Alt")
        return "" if v is None else str(v)
    except Exception:
        return ""


# alt text that satisfies the rule and tells a screen-reader user nothing.
# Word writes the source filename ("image0.jpeg") or its own object name
# ("Picture 3") into /Alt, and veraPDF counts that as fully compliant.
# We never overwrite it - it is authored content - but we do report it.
_USELESS_ALT = re.compile(
    r"^(?:"
    r"[\w\-. ]+\.(?:png|jpe?g|gif|bmp|tif{1,2}|emf|wmf|svg|webp|eps|pdf)"
    r"|(?:picture|image|img|graphic|figure|diagram|chart|object|shape|"
    r"screenshot|screen ?shot|photo|clip ?art)\s*[-_ #]?\d*"
    r"|dsc[_-]?\d+|img[_-]?\d+|untitled\s*\d*"
    r")$", re.I)


def alt_is_useless(text):
    """True when /Alt is present but is plainly not a description."""
    t = (text or "").strip().strip(".")
    if not t:
        return False
    return bool(_USELESS_ALT.match(t))


def alt_existing_figures(pdf, notes):
    """Producer trees ship Figure elements with missing or EMPTY /Alt.
    Setting /Alt is a plain key write (no restructuring) so it is safe on a
    foreign tree, but a placeholder only clears the rule - it does not make
    the image accessible, so it is deliberately the ONE string the alt pass
    hunts for later (see PLACEHOLDER_ALT).
    Returns (n_placeholders_set, n_figures_total)."""
    figs = walk_figures(pdf)
    fixed = 0
    for _ordinal, node, _pi in figs:
        if _alt_missing(node):
            node.Alt = String(PLACEHOLDER_ALT)
            fixed += 1
    if fixed:
        notes.append("%d of %d producer-tree figure(s) had missing or EMPTY "
                     "alt - placeholder set to satisfy 7.3-1 and queued for a "
                     "real description pass" % (fixed, len(figs)))
    return fixed, len(figs)


def stamp_pdfua_xmp(pdf):
    """pdfuaid:part=1 in XMP (PDF/UA-1 rule 5). pikepdf knows the pdfuaid
    namespace in recent versions; fall back to raw XMP surgery if not."""
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["pdfuaid:part"] = "1"
        return True
    except Exception:
        pass
    try:
        raw = pdf.Root.Metadata.read_bytes().decode("utf-8", "replace")
        if "pdfuaid" in raw:
            return True
        ins = ('<rdf:Description rdf:about="" '
               'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">'
               '<pdfuaid:part>1</pdfuaid:part></rdf:Description>')
        if "</rdf:RDF>" not in raw:
            return False
        raw = raw.replace("</rdf:RDF>", ins + "</rdf:RDF>", 1)
        pdf.Root.Metadata = pdf.make_stream(raw.encode("utf-8"))
        return True
    except Exception:
        return False


# ---------------------------------------------------------- font embedding

# PDF/UA-1 7.21.4.1 requires every rendering font EMBEDDED. Word exports
# reference Windows system fonts without embedding them - the single biggest
# residual on the math corpus (35 font-only files). For fonts that exist on
# this machine the fix is mechanical: attach the SAME font file the viewer
# was already substituting, so rendering is unchanged (and the render-verify
# gate proves it per file). Full embed, no subsetting - bigger files, zero
# glyph-coverage risk.
WINFONTS = r"C:\Windows\Fonts"
FONT_MAP = {
    "arial": "arial.ttf", "arialmt": "arial.ttf", "helvetica": "arial.ttf",
    "arial,bold": "arialbd.ttf", "arial-boldmt": "arialbd.ttf",
    "helvetica-bold": "arialbd.ttf",
    "arial,italic": "ariali.ttf", "arial-italicmt": "ariali.ttf",
    "helvetica-oblique": "ariali.ttf",
    "arial,bolditalic": "arialbi.ttf", "arial-bolditalicmt": "arialbi.ttf",
    "helvetica-boldoblique": "arialbi.ttf",
    "timesnewroman": "times.ttf", "timesnewromanpsmt": "times.ttf",
    "times-roman": "times.ttf",
    "timesnewroman,bold": "timesbd.ttf", "timesnewromanps-boldmt": "timesbd.ttf",
    "times-bold": "timesbd.ttf",
    "timesnewroman,italic": "timesi.ttf", "timesnewromanps-italicmt": "timesi.ttf",
    "times-italic": "timesi.ttf",
    "timesnewroman,bolditalic": "timesbi.ttf",
    "timesnewromanps-bolditalicmt": "timesbi.ttf",
    "tahoma": "tahoma.ttf", "tahoma,bold": "tahomabd.ttf",
    "couriernew": "cour.ttf", "couriernewpsmt": "cour.ttf", "courier": "cour.ttf",
    "couriernew,bold": "courbd.ttf", "couriernewps-boldmt": "courbd.ttf",
    "couriernew,italic": "couri.ttf", "couriernewps-italicmt": "couri.ttf",
    "symbol": "symbol.ttf",
    "verdana": "verdana.ttf", "verdana,bold": "verdanab.ttf",
    "calibri": "calibri.ttf", "calibri,bold": "calibrib.ttf",
    "georgia": "georgia.ttf", "cambria": "cambria.ttc",
}


# Symbol-font construction pieces (tall delimiters built from segments);
# not in the AGL but they have Unicode points, and Segoe UI Symbol covers
# them when Arial doesn't
SYMBOL_PIECE_UV = {
    "parenlefttp": 0x239B, "parenleftex": 0x239C, "parenleftbt": 0x239D,
    "parenrighttp": 0x239E, "parenrightex": 0x239F, "parenrightbt": 0x23A0,
    "bracketlefttp": 0x23A1, "bracketleftex": 0x23A2,
    "bracketleftbt": 0x23A3, "bracketrighttp": 0x23A4,
    "bracketrightex": 0x23A5, "bracketrightbt": 0x23A6,
    "bracelefttp": 0x23A7, "braceleftmid": 0x23A8, "braceleftbt": 0x23A9,
    "braceex": 0x23AA, "bracerighttp": 0x23AB, "bracerightmid": 0x23AC,
    "bracerightbt": 0x23AD, "integraltp": 0x2320, "integralex": 0x23AE,
    "integralbt": 0x2321, "arrowvertex": 0x23D0,
}


def _glyph_name_uv(nm):
    from fontTools.agl import AGL2UV
    u = AGL2UV.get(nm)
    if u is None and re.match(r"^uni[0-9A-Fa-f]{4}$", nm or ""):
        u = int(nm[3:], 16)
    if u is None:
        u = SYMBOL_PIECE_UV.get(nm)
    return u


def _synth_diff_ttf(src_path, code2uv):
    """Build a tiny SYMBOLIC TrueType covering a /Differences-encoded
    font's codes: subset the source to the needed glyphs and remap them
    into a (3,0) cmap at F000+code. Returns (ttf bytes, {code: width})."""
    import io
    from fontTools.ttLib import TTFont
    from fontTools.subset import Subsetter, Options
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_4
    t = TTFont(src_path, fontNumber=0)
    opts = Options()
    opts.notdef_outline = True
    ss = Subsetter(opts)
    ss.populate(unicodes=list(code2uv.values()))
    ss.subset(t)
    cm = t.getBestCmap() or {}
    st4 = cmap_format_4(4)
    st4.platformID, st4.platEncID, st4.language = 3, 0, 0
    st4.cmap = {0xF000 | c: cm[u] for c, u in code2uv.items() if u in cm}
    t["cmap"].tables = [st4]
    upem = t["head"].unitsPerEm or 1000
    k = 1000.0 / upem
    hmtx = t["hmtx"]
    widths = {}
    for c in code2uv:
        g = st4.cmap.get(0xF000 | c)
        if g:
            widths[c] = int(round(hmtx[g][0] * k))
    buf = io.BytesIO()
    t.save(buf)
    t.close()
    return buf.getvalue(), widths


def _resolve_font_file(basefont):
    name = re.sub(r"^[A-Z]{6}\+", "", str(basefont).lstrip("/")).lower()
    fn = FONT_MAP.get(name)
    if fn:
        p = os.path.join(WINFONTS, fn)
        return p if os.path.isfile(p) and not p.endswith(".ttc") else None
    return None


def _ttf_metrics(path):
    """FontDescriptor numbers from the TTF, scaled to 1000/em."""
    from fontTools.ttLib import TTFont
    tt = TTFont(path, fontNumber=0, lazy=True)
    upem = tt["head"].unitsPerEm or 1000
    k = 1000.0 / upem
    head = tt["head"]
    bbox = [int(head.xMin * k), int(head.yMin * k),
            int(head.xMax * k), int(head.yMax * k)]
    hhea = tt["hhea"]
    os2 = tt.get("OS/2")
    cap = int(getattr(os2, "sCapHeight", 0) * k) if os2 else 0
    if not cap:
        cap = int(hhea.ascent * k * 0.9)
    weight = getattr(os2, "usWeightClass", 400) if os2 else 400
    italic = float(getattr(tt.get("post"), "italicAngle", 0) or 0)
    m = {"bbox": bbox, "ascent": int(hhea.ascent * k),
         "descent": int(hhea.descent * k), "cap": cap,
         "stemv": 50 + int((weight / 65.0) ** 2), "italic": italic,
         "upem": upem}
    # widths for a synthesized /Widths (32..255): try (3,1) unicode, then
    # (3,0) symbol at 0xF000|code, then (1,0)
    cmap = tt.getBestCmap() or {}
    sym = {}
    for table in tt["cmap"].tables:
        if table.platformID == 3 and table.platEncID == 0:
            sym = table.cmap
            break
    hmtx = tt["hmtx"]
    widths = []
    for code in range(32, 256):
        # the byte CODE must be decoded to Unicode before the cmap lookup:
        # WinAnsi 0x92 is U+2019, not codepoint 146 - raw-code lookups gave
        # width 0 for every non-ASCII glyph (veraPDF 7.21.5 caught it)
        try:
            u = ord(bytes([code]).decode("cp1252"))
        except Exception:
            u = None
        g = ((cmap.get(u) if u is not None else None)
             or sym.get(0xF000 | code) or sym.get(code) or cmap.get(code))
        try:
            # ROUND, don't truncate: veraPDF 7.21.5 compares /Widths against
            # the font program's advance widths; int() is off-by-one on half
            # the glyphs and fails the consistency check
            widths.append(int(round(hmtx[g][0] * k)) if g else 0)
        except Exception:
            widths.append(0)
    m["widths"] = widths
    # unicode -> width, for /Differences-encoded fonts whose widths come
    # from glyph names rather than cp1252 codes
    uw = {}
    for u, g in (cmap or {}).items():
        try:
            uw[u] = int(round(hmtx[g][0] * k))
        except Exception:
            pass
    m["uwidths"] = uw
    tt.close()
    return m


def embed_missing_fonts(pdf, notes, convert_type1=True):
    """Attach FontFile2 to every unembedded simple font that maps to a local
    Windows font. Existing FontDescriptors just gain /FontFile2; a bare
    standard-14-style font (no descriptor - Word's /Symbol) gets a
    synthesized descriptor + widths and, for TrueType handling, its Subtype
    switched from Type1. Returns fonts embedded; unmapped fonts are noted."""
    cache = {}          # ttf path -> (stream ref, metrics)
    mcache = {}         # ttf path -> metrics only (no stream created)
    embedded = 0
    unmapped = set()

    def get_metrics(path):
        if path in cache:
            return cache[path][1]
        if path not in mcache:
            mcache[path] = _ttf_metrics(path)
        return mcache[path]

    def get_stream(path):
        if path not in cache:
            data = open(path, "rb").read()
            st = pdf.make_stream(data)
            st.Length1 = len(data)
            cache[path] = (st, mcache.get(path) or _ttf_metrics(path))
        return cache[path]

    def fix_font(f):
        nonlocal embedded
        sub = str(f.get("/Subtype"))
        if sub not in ("/TrueType", "/Type1"):
            return
        fd = f.get("/FontDescriptor")
        if fd is not None and any(fd.get(x) is not None
                                  for x in ("/FontFile", "/FontFile2", "/FontFile3")):
            return
        # bare base-14 Symbol/ZapfDingbats with the BUILT-IN encoding:
        # PyMuPDF bundles metric-compatible CFF programs for exactly these
        # - attach as FontFile3/Type1C and nothing else changes (no
        # conversion, no encoding surgery, extraction identical), so this
        # is safe in every tier
        base_key = re.sub(r"^[A-Z]{6}\+", "",
                          str(f.get("/BaseFont") or "").lstrip("/")).lower()
        enc0 = f.get("/Encoding")
        has_diffs = (enc0 is not None and hasattr(enc0, "get")
                     and enc0.get("/Differences") is not None)
        if sub == "/Type1" and not has_diffs \
                and base_key in ("symbol", "zapfdingbats"):
            try:
                import pymupdf as fitz
                import io as _io
                from fontTools.cffLib import CFFFontSet
                buf = bytes(fitz.Font(
                    "symb" if base_key == "symbol" else "zadb").buffer)
                st3 = pdf.make_stream(buf)
                st3.Subtype = Name("/Type1C")
                cf = CFFFontSet()
                cf.decompile(_io.BytesIO(buf), None)
                td = cf[cf.fontNames[0]]
                bbox = [int(v) for v in
                        td.rawDict.get("FontBBox", [-200, -300, 1100, 1000])]
                if fd is None:
                    fd = pdf.make_indirect(Dictionary(
                        Type=Name.FontDescriptor,
                        FontName=f.get("/BaseFont"), Flags=4,
                        FontBBox=Array(bbox), ItalicAngle=0,
                        Ascent=bbox[3], Descent=bbox[1],
                        CapHeight=bbox[3], StemV=85))
                    f.FontDescriptor = fd
                fd.FontFile3 = st3
                # producers ship zeroed /Widths for base-14 fonts; once a
                # program is embedded veraPDF compares them (7.21.5) -
                # rebuild the array from the CFF charstrings via the
                # font's built-in encoding
                try:
                    from fontTools.pens.basePen import NullPen
                    cs = td.CharStrings
                    cffenc = td.Encoding
                    arr = []
                    for code in range(32, 256):
                        nm = (cffenc[code]
                              if code < len(cffenc) else ".notdef")
                        w = 0
                        if nm != ".notdef" and nm in cs:
                            c = cs[nm]
                            c.draw(NullPen())   # sets .width as a side effect
                            w = int(round(c.width))
                        arr.append(w)
                    f.FirstChar = 32
                    f.LastChar = 255
                    f.Widths = Array(arr)
                except Exception:
                    pass
                embedded += 1
            except Exception:
                pass
            return
        if sub == "/Type1" and not convert_type1:
            if "symbol" in base_key or "dingbat" in base_key:
                return   # symbolic conversions change extraction (their
                         # /Encoding must be dropped); nonsymbolic Base-14
                         # keep WinAnsi and convert safely
        path = _resolve_font_file(f.get("/BaseFont"))
        if not path:
            unmapped.add(str(f.get("/BaseFont")))
            return
        # a /Differences encoding means the CODES are custom but the GLYPH
        # NAMES carry the meaning - the font behaves nonsymbolic no matter
        # what it's called. Embedding symbol.ttf (cmap (3,0) only, no glyph
        # names) would orphan every code; pick a face that covers the
        # names' Unicode points instead.
        diffs = None
        enc = f.get("/Encoding")
        if enc is not None and hasattr(enc, "get"):
            diffs = enc.get("/Differences")
        code2name = {}
        if diffs is not None:
            cur_c = None
            for item in diffs:
                try:
                    cur_c = int(item)
                    continue
                except (TypeError, ValueError):
                    pass
                if cur_c is not None:
                    code2name[cur_c] = str(item).lstrip("/")
                    cur_c += 1
        if code2name and os.path.basename(path).startswith("symbol"):
            from fontTools.agl import AGL2UV
            need = {c: _glyph_name_uv(nm) for c, nm in code2name.items()}
            if any(u is None for u in need.values()):
                unmapped.add(str(f.get("/BaseFont")))
                return
            arial = os.path.join(WINFONTS, "arial.ttf")
            plain = (os.path.isfile(arial)
                     and all(nm in AGL2UV for nm in code2name.values())
                     and all(u in get_metrics(arial)["uwidths"]
                             for u in need.values()))
            if plain:
                # every name is proper AGL and Arial covers it: embed
                # Arial nonsymbolic, keep the Differences encoding
                path = arial
            else:
                # piece names (parenleftex...) resolve through Adobe's
                # corporate PUA, which no live font covers, and non-AGL
                # names break 7.21.6 - synthesize a symbolic font with
                # the glyphs at (3,0) F000+code instead
                src = None
                for cand in ("seguisym.ttf", "arial.ttf"):
                    cp = os.path.join(WINFONTS, cand)
                    if not os.path.isfile(cp):
                        continue
                    try:
                        if all(u in get_metrics(cp)["uwidths"]
                               for u in need.values()):
                            src = cp
                            break
                    except Exception:
                        continue
                if src is None:
                    unmapped.add(str(f.get("/BaseFont")))
                    return
                data, wmap = _synth_diff_ttf(src, need)
                st2 = pdf.make_stream(data)
                st2.Length1 = len(data)
                mm = get_metrics(src)
                subname = Name("/CFSYMA+SegoeUISymbol")
                f.FontDescriptor = pdf.make_indirect(Dictionary(
                    Type=Name.FontDescriptor, FontName=subname, Flags=4,
                    FontBBox=Array(mm["bbox"]), ItalicAngle=mm["italic"],
                    Ascent=mm["ascent"], Descent=mm["descent"],
                    CapHeight=mm["cap"], StemV=mm["stemv"],
                    FontFile2=st2))
                f.BaseFont = subname
                f.Subtype = Name("/TrueType")
                lo, hi = min(need), max(need)
                f.FirstChar = lo
                f.LastChar = hi
                f.Widths = Array([wmap.get(c, 0) for c in range(lo, hi + 1)])
                if f.get("/Encoding") is not None:
                    del f["/Encoding"]      # symbolic TrueType: cmap only
                bf = ["/CIDInit /ProcSet findresource begin",
                      "12 dict begin", "begincmap",
                      "/CMapName /CF-Sym def", "/CMapType 2 def",
                      "1 begincodespacerange <00> <FF> endcodespacerange",
                      "%d beginbfchar" % len(need)]
                for c in sorted(need):
                    bf.append("<%02X> <%04X>" % (c, need[c]))
                bf += ["endbfchar", "endcmap",
                       "CMapName currentdict /CMap defineresource pop",
                       "end", "end"]
                f.ToUnicode = pdf.make_stream("\n".join(bf).encode("ascii"))
                embedded += 1
                return
        st, m = get_stream(path)
        symbolic = (os.path.basename(path).startswith("symbol")
                    and not code2name)
        if fd is None:
            fd = pdf.make_indirect(Dictionary(
                Type=Name.FontDescriptor, FontName=f.get("/BaseFont"),
                Flags=(4 if symbolic else 32), FontBBox=Array(m["bbox"]),
                ItalicAngle=m["italic"], Ascent=m["ascent"],
                Descent=m["descent"], CapHeight=m["cap"], StemV=m["stemv"]))
            f.FontDescriptor = fd
        elif symbolic:
            # veraPDF resolves glyphs by the descriptor flags: nonsymbolic
            # goes through (3,1) Unicode, which symbol.ttf doesn't have -
            # every glyph reads as undefined (7.21.4.1-2)
            fd.Flags = 4
        fd.FontFile2 = st
        if sub == "/Type1":
            # the font program we attach is a TTF; the dict must say so
            f.Subtype = Name("/TrueType")
        if code2name:
            # widths must match the swapped-in program, and codes resolve
            # through the Differences names - not cp1252
            lo, hi = min(code2name), max(code2name)
            arr = []
            for c in range(lo, hi + 1):
                u = _glyph_name_uv(code2name.get(c) or "")
                arr.append(m["uwidths"].get(u, 0) if u is not None else 0)
            f.FirstChar = lo
            f.LastChar = hi
            f.Widths = Array(arr)
            if enc is not None and hasattr(enc, "get"):
                if enc.get("/BaseEncoding") is None:
                    # nonsymbolic TrueType + Differences requires a declared
                    # base encoding (veraPDF 7.21.4.1)
                    enc.BaseEncoding = Name("/WinAnsiEncoding")
                # legacy piece names (parenleftex...) resolve through
                # Adobe's corporate PUA (F8xx), which no live font covers;
                # the uniXXXX spelling resolves to the real codepoint
                newdiffs = []
                renamed = False
                for item in diffs:
                    try:
                        newdiffs.append(int(item))
                        continue
                    except (TypeError, ValueError):
                        pass
                    nm = str(item).lstrip("/")
                    if nm in SYMBOL_PIECE_UV:
                        newdiffs.append(Name("/uni%04X" % SYMBOL_PIECE_UV[nm]))
                        renamed = True
                    else:
                        newdiffs.append(Name("/" + nm))
                if renamed:
                    enc.Differences = Array(newdiffs)
        elif symbolic or f.get("/Widths") is None:
            # symbolic swaps must also swap /Widths: the producer's AFM
            # Symbol metrics disagree with symbol.ttf advances (7.21.5-1)
            f.FirstChar = 32
            f.LastChar = 255
            f.Widths = Array(m["widths"])
        if symbolic and f.get("/Encoding") is not None:
            del f["/Encoding"]        # symbolic TrueType: cmap only
        embedded += 1

    for f in _iter_font_dicts(pdf):
        try:
            fix_font(f)
        except Exception:
            continue
    if embedded:
        notes.append("embedded %d system font(s) (full TTF, render-verified)"
                     % embedded)
    if unmapped:
        notes.append("unembedded font(s) with no local match: %s"
                     % ", ".join(sorted(unmapped)[:6]))
    return embedded


def set_metadata(pdf, title):
    pdf.Root.Lang = String("en-US")
    vp = pdf.Root.get("/ViewerPreferences")
    if vp is None:
        pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    else:
        vp.DisplayDocTitle = True
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:title"] = title
    pdf.docinfo["/Title"] = title


# ------------------------------------------------------------ figures/alt
#
# ONE enumeration, ONE file. collect_alt_todo and apply_alt both walk the
# FIXED pdf with walk_figures(), so a figure's ordinal cannot drift between
# the two stages the way it did when process time recorded an index into one
# sequence and apply time indexed into another.
#
# Picking the picture a describer looks at is the other half. A Figure points
# at marked content, not at an image, so the pairing is positional per page -
# but "positional against page.get_images()" silently produced NOTHING for
# vector figures (Word shapes, charts and SmartArt have no image XObject: 16
# of 21 figures on the live math course extracted as None and shipped with the
# placeholder while the run still reported compliant). So targets are built in
# descending order of fidelity - real image pixels, then a clip render of a
# vector cluster, then the whole page - and whatever a figure ends up with is
# LABELLED so the report and the prompt can both tell the truth about it.

FIG_MIN_PIXELS = 40          # smaller than this on a side: rule/bullet, not art
FIG_CLIP_DPI = 216           # clip renders: small diagrams still readable
FIG_MIN_CLIP_PT = 8.0        # degenerate bbox (a text origin, a dot) -> unusable


def _mat_mul(m, n):
    """PDF 6-tuple matrix product, m then n."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _figure_mcids(node):
    """Marked-content ids a Figure StructElem points at: bare ints in /K, and
    /MCR dictionaries. This is how a Figure says WHERE it is on the page - far
    better than guessing positionally, and the only thing that works for a
    Word shape drawn as vectors with no image XObject at all."""
    out = []
    try:
        k = node.get("/K")
    except Exception:
        return out
    if k is None:
        return out
    items = list(k) if isinstance(k, (Array, list)) else [k]
    for it in items:
        try:
            if isinstance(it, int):
                out.append(int(it))
            elif hasattr(it, "get") and it.get("/MCID") is not None:
                out.append(int(it.get("/MCID")))
        except Exception:
            continue
    return out


def _mcid_bboxes(page):
    """{mcid: (x0, y0, x1, y1)} in PDF user space, by replaying the page
    content stream with a CTM stack and accumulating every drawing point
    inside each BDC..EMC region. Costs one parse per page, only paid for pages
    that actually have a figure waiting for a description."""
    boxes = {}
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    gs_stack, mc_stack, tm = [], [], None
    try:
        instrs = pikepdf.parse_content_stream(page)
    except Exception:
        return boxes

    def add(mcid, x, y):
        a, b, c, d, e, f = ctm
        px, py = a * x + c * y + e, b * x + d * y + f
        box = boxes.get(mcid)
        if box is None:
            boxes[mcid] = [px, py, px, py]
        else:
            box[0] = min(box[0], px); box[1] = min(box[1], py)
            box[2] = max(box[2], px); box[3] = max(box[3], py)

    cur = None
    for ins in instrs:
        op = _op(ins)
        ops = list(ins.operands)
        try:
            if op == "q":
                gs_stack.append(ctm)
                continue
            if op == "Q":
                if gs_stack:
                    ctm = gs_stack.pop()
                continue
            if op == "cm" and len(ops) == 6:
                ctm = _mat_mul(tuple(float(o) for o in ops), ctm)
                continue
            if op in ("BDC", "BMC"):
                mcid = None
                if op == "BDC" and len(ops) >= 2 and hasattr(ops[1], "get"):
                    v = ops[1].get("/MCID")
                    if v is not None:
                        mcid = int(v)
                mc_stack.append(mcid)
                cur = next((m for m in reversed(mc_stack) if m is not None), None)
                continue
            if op == "EMC":
                if mc_stack:
                    mc_stack.pop()
                cur = next((m for m in reversed(mc_stack) if m is not None), None)
                continue
            if cur is None:
                continue
            if op in ("m", "l") and len(ops) == 2:
                add(cur, float(ops[0]), float(ops[1]))
            elif op == "c" and len(ops) == 6:
                for i in (0, 2, 4):
                    add(cur, float(ops[i]), float(ops[i + 1]))
            elif op in ("v", "y") and len(ops) == 4:
                for i in (0, 2):
                    add(cur, float(ops[i]), float(ops[i + 1]))
            elif op == "re" and len(ops) == 4:
                x, y, w, h = (float(o) for o in ops)
                add(cur, x, y)
                add(cur, x + w, y + h)
            elif op == "Do":
                # an XObject occupies the CTM-transformed unit square
                for cx, cy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    add(cur, cx, cy)
            elif op == "Tm" and len(ops) == 6:
                tm = tuple(float(o) for o in ops)
                add(cur, tm[4], tm[5])
            elif op in ("Td", "TD") and len(ops) == 2 and tm is not None:
                tm = _mat_mul((1, 0, 0, 1, float(ops[0]), float(ops[1])), tm)
                add(cur, tm[4], tm[5])
        except Exception:
            continue
    return {k: tuple(v) for k, v in boxes.items()}


def _pdf_rect_to_page(page, bbox):
    """PDF user space -> the page's DISPLAYED fitz coordinates.
    transformation_matrix flips y in UNROTATED space, so a /Rotate 90 page
    (landscape math handouts, scanned sheets) needs rotation_matrix after it
    or every clip lands off-page. Identity for unrotated pages."""
    r = fitz.Rect(*bbox) * page.transformation_matrix * page.rotation_matrix
    r.normalize()
    return r


def _reading_order(points):
    """Indices of (x, y) points in human reading order (top band, then left to
    right). Rounding y to a 12pt band keeps a row of side-by-side figures
    together instead of interleaving them by a stray baseline difference."""
    return sorted(range(len(points)),
                  key=lambda i: (round(points[i][1] / 12.0), points[i][0]))


def _raster_targets(page):
    """[(bbox, xref)] for images big enough to be content, in reading order."""
    out = []
    try:
        for info in page.get_image_info(xrefs=True):
            bbox = info.get("bbox")
            xref = info.get("xref", 0)
            if not bbox:
                continue
            w, h = info.get("width", 0), info.get("height", 0)
            if min(w, h) < FIG_MIN_PIXELS:
                continue
            if (max(w, h) / max(1, min(w, h))) >= DECOR_MAX_ASPECT:
                continue
            out.append((tuple(bbox), xref))
    except Exception:
        return []
    order = _reading_order([(b[0], b[1]) for b, _x in out])
    return [out[i] for i in order]


def _vector_targets(page):
    """Bounding boxes of clustered vector drawings - the Word-shape case that
    has no image XObject at all. Nearby paths are merged so one chart comes
    back as one box instead of four hundred line segments."""
    rects = []
    try:
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r is None or r.is_empty:
                continue
            if min(r.width, r.height) < 8 or max(r.width, r.height) < 24:
                continue          # rules, underlines, table borders
            rects.append(fitz.Rect(r))
    except Exception:
        return []
    merged = []
    for r in rects:
        hit = None
        for m in merged:
            probe = fitz.Rect(m) + (-12, -12, 12, 12)   # 12pt gap = same figure
            if probe.intersects(r):
                hit = m
                break
        if hit is None:
            merged.append(fitz.Rect(r))
        else:
            hit |= r
    page_area = abs(page.rect.get_area()) or 1.0
    merged = [m for m in merged
              if m.width >= 36 and m.height >= 36
              and abs(m.get_area()) < page_area * 0.98]
    order = _reading_order([(m.x0, m.y0) for m in merged])
    return [merged[i] for i in order]


def _save_pixmap(pix, path):
    """Write a PNG a vision model can actually read; returns (w, h)."""
    if pix.colorspace is None or pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    pix.save(path)
    return pix.width, pix.height


def _clip_render(page, rect, target, notes):
    """Render one region of a page to PNG. Returns (w, h, hash) or None."""
    import hashlib
    rect = fitz.Rect(rect) + (-6, -6, 6, 6)      # a little air around the art
    rect &= page.rect
    if rect.is_empty or min(rect.width, rect.height) < FIG_MIN_CLIP_PT:
        return None
    zoom = FIG_CLIP_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect,
                          alpha=False)
    if pix.width < 8 or pix.height < 8:
        return None
    w, h = _save_pixmap(pix, target)
    with open(target, "rb") as fh:
        digest = hashlib.md5(fh.read()).hexdigest()[:16]
    return w, h, digest


def _page_figure_pngs(doc, pi, page_rows, fig_dir, notes):
    """One picture per Figure on page pi, best available fidelity:

      region   the figure's OWN marked content, located by MCID - exact, and
               the only thing that works for vector art (Word shapes/charts)
      image    a raster image XObject paired positionally, original pixels
      drawing  a clustered vector bounding box, paired positionally
      page     the whole page, used ONLY when the figure is the sole figure on
               it (otherwise which figure is which is a guess, and a guess is
               what shipped meaningless placeholders before)

    Every row gets `png`/`kind`, or nothing at all - and "nothing" is reported,
    never silently swallowed."""
    n = len(page_rows)
    if pi is None or pi < 0 or pi >= doc.page_count:
        return
    page = doc[pi]
    # positional fallbacks, only computed if MCIDs do not cover everything
    fallbacks = None
    for slot_i, row in enumerate(page_rows):
        target = os.path.join(fig_dir, "p%03d-f%02d.png" % (pi, slot_i))
        try:
            got = None
            if row.get("mcbox"):
                got = _clip_render(page, _pdf_rect_to_page(page, row["mcbox"]),
                                   target, notes)
                if got:
                    row.update(png=target, kind="region", width=got[0],
                               height=got[1], hash=got[2])
                    continue
            if fallbacks is None:
                fallbacks = [("image", b, x) for b, x in _raster_targets(page)]
                if len(fallbacks) < n:
                    fallbacks += [("drawing", tuple(r), None)
                                  for r in _vector_targets(page)]
            if slot_i < len(fallbacks):
                kind, bbox, xref = fallbacks[slot_i]
                if kind == "image" and xref:
                    import hashlib
                    raw = doc.extract_image(xref)
                    digest = hashlib.md5(raw["image"]).hexdigest()[:16]
                    w, h = _save_pixmap(fitz.Pixmap(doc, xref), target)
                    row.update(png=target, kind="image", width=w, height=h,
                               hash=digest)
                    continue
                got = _clip_render(page, fitz.Rect(bbox), target, notes)
                if got:
                    row.update(png=target, kind="drawing", width=got[0],
                               height=got[1], hash=got[2])
                    continue
            if n == 1:
                # sole figure on the page: the page IS an honest picture of it
                got = _clip_render(page, page.rect, target, notes)
                if got:
                    row.update(png=target, kind="page", width=got[0],
                               height=got[1], hash=got[2])
        except Exception as e:
            notes.append("page %d figure %d: could not render a picture (%s)"
                         % (pi + 1, slot_i + 1, e))


def figure_inventory(fixed_pdf, fig_dir, notes=None):
    """Every Figure in fixed_pdf: its stable tree ordinal, its page, its
    current /Alt, and the best picture we can show a describer. The whole
    figure/alt contract in one function, read off ONE file."""
    notes = notes if notes is not None else []
    rows = []
    try:
        with pikepdf.open(fixed_pdf) as pdf:
            figs = walk_figures(pdf)
            rows = [{"ordinal": o, "page": pi, "alt": _alt_text(n),
                     "mcids": _figure_mcids(n)} for o, n, pi in figs]
            need_pages = {r["page"] for r in rows
                          if r["alt"].strip() in ("", PLACEHOLDER_ALT)
                          and r["page"] is not None}
            for pi in sorted(need_pages):
                try:
                    boxes = _mcid_bboxes(pdf.pages[pi])
                except Exception:
                    continue
                for r in rows:
                    if r["page"] != pi or not r["mcids"]:
                        continue
                    hit = [boxes[m] for m in r["mcids"] if m in boxes]
                    if hit:
                        r["mcbox"] = (min(b[0] for b in hit),
                                      min(b[1] for b in hit),
                                      max(b[2] for b in hit),
                                      max(b[3] for b in hit))
    except Exception as e:
        notes.append("could not read the tag tree of %s (%s)"
                     % (os.path.basename(fixed_pdf), e))
        return []
    if not rows:
        return []
    if not any(r["alt"].strip() in ("", PLACEHOLDER_ALT) for r in rows):
        return rows                       # everything already described
    os.makedirs(fig_dir, exist_ok=True)
    by_page = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)
    doc = None
    try:
        doc = fitz.open(fixed_pdf)
        for pi, page_rows in by_page.items():
            _page_figure_pngs(doc, pi, page_rows, fig_dir, notes)
    except Exception as e:
        notes.append("could not render pictures for %s (%s)"
                     % (os.path.basename(fixed_pdf), e))
    finally:
        if doc is not None:
            doc.close()
    return rows


def collect_alt_todo(workdir, quiet=False):
    """Aggregate every figure still holding the placeholder, deduped by image
    hash -> alt-todo.json. A describer (the model, or the app's Claude step)
    views each png and writes alt.json {hash: "alt text" | "" for decorative};
    apply_alt pushes the words back into the tag trees.

    Only figures that STILL carry PLACEHOLDER_ALT are listed, so running the
    description step twice does not re-describe (and re-bill) the whole course
    and cannot overwrite a description that is already in place.

    Figures with no renderable picture are COUNTED AND NAMED rather than
    dropped: shipping a meaningless placeholder under a green report was the
    single most misleading thing this pipeline did."""
    todo, described, unpicturable, useless = {}, 0, [], []
    for d in sorted(os.listdir(workdir)):
        sub = os.path.join(workdir, d)
        fixed = os.path.join(sub, "fixed.pdf")
        if not os.path.isfile(fixed):
            continue
        name = d
        mp = os.path.join(sub, "file.json")
        if os.path.isfile(mp):
            try:
                name = json.load(open(mp, encoding="utf-8-sig"))["display_name"]
            except Exception:
                pass
        for r in figure_inventory(fixed, os.path.join(sub, "figures")):
            if r["alt"].strip() not in ("", PLACEHOLDER_ALT):
                described += 1
                if alt_is_useless(r["alt"]):
                    useless.append((name, r["ordinal"], r["alt"].strip()))
                continue
            if not r.get("png"):
                unpicturable.append((name, r["ordinal"]))
                continue
            e = todo.setdefault(r["hash"], {
                "png": r["png"], "kind": r["kind"], "width": r["width"],
                "height": r["height"], "used_by": []})
            e["used_by"].append({"dir": sub, "file": name,
                                 "ordinal": r["ordinal"], "page": r["page"]})
    out = os.path.join(workdir, "alt-todo.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(todo, fh, indent=1)
    summary = {"needs_alt": sum(len(e["used_by"]) for e in todo.values()),
               "unique_images": len(todo),
               "already_described": described,
               "no_picture_available": len(unpicturable),
               "no_picture_files": ["%s figure #%d" % (f, o + 1)
                                    for f, o in unpicturable[:200]],
               "useless_existing_alt": len(useless),
               "useless_existing_alt_files": [
                   "%s figure #%d: %r" % (f, o + 1, t)
                   for f, o, t in useless[:200]]}
    with open(os.path.join(workdir, "alt-summary.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print("%d figure(s) need alt text across %d unique image(s) -> %s"
          % (summary["needs_alt"], len(todo), out))
    if described:
        print("  %d figure(s) already described - left alone" % described)
    if unpicturable:
        print("  %d figure(s) have NO renderable picture: they keep the "
              "placeholder and need a person" % len(unpicturable))
        for fname, ordinal in unpicturable[:10]:
            print("     %s  figure #%d" % (fname, ordinal + 1))
        if len(unpicturable) > 10:
            print("     ... and %d more (see alt-summary.json)"
                  % (len(unpicturable) - 10))
    if useless:
        # left as-is on purpose (it is authored content) but a green PDF/UA
        # report on "image0.jpeg" is exactly the false comfort to call out
        print("  %d figure(s) have alt text that is only a filename or "
              "'Picture 3' - it passes a compliance scan but describes "
              "nothing. Left as the author wrote it; worth a human pass:"
              % len(useless))
        for fname, ordinal, text in useless[:10]:
            print("     %s  figure #%d: %r" % (fname, ordinal + 1, text[:50]))
        if len(useless) > 10:
            print("     ... and %d more (see alt-summary.json)"
                  % (len(useless) - 10))
    if not quiet:
        for h, e in todo.items():
            print("  %s  %-8s %dx%d  used %d time(s)  %s"
                  % (h, e["kind"], e["width"], e["height"],
                     len(e["used_by"]), e["png"]))
    return 0


def apply_alt(workdir, alt_path):
    """Write authored alt into the fixed PDFs. alt.json maps
    hash -> alt text ('' = decorative).

    Three guarantees that were missing and cost real alt text:
      - a figure is written ONLY if it still holds PLACEHOLDER_ALT (or nothing),
        so a description already in the file - the author's, or an earlier
        run's - can never be overwritten by an ordinal that drifted;
      - the save is staged and atomically replaced, so a Defender/OneDrive/
        indexer lock cannot leave a half-written fixed.pdf behind;
      - the pre-alt file is kept as fixed.pdf.prealt. alt is written into a PDF
        that verify already blessed, so the caller re-verifies afterwards and
        restores this copy on failure - an alt write must never be the
        unverified thing that reaches Canvas.
    """
    alt = json.load(open(alt_path, encoding="utf-8-sig"))
    todo_path = os.path.join(workdir, "alt-todo.json")
    todo = json.load(open(todo_path, encoding="utf-8-sig"))
    touched = {}
    for h, text in alt.items():
        if h not in todo:
            print("WARN: hash %s not in alt-todo.json (skipped)" % h)
            continue
        for use in todo[h]["used_by"]:
            touched.setdefault(use["dir"], []).append((use["ordinal"], text))
    n_files = n_alts = n_skipped = 0
    for d, pairs in sorted(touched.items()):
        fixed = os.path.join(d, "fixed.pdf")
        if not os.path.isfile(fixed):
            print("WARN: no fixed.pdf in %s" % d)
            continue
        wrote = 0
        staged = fixed + ".part"
        # save INSIDE the context, replace AFTER it closes: Windows will not
        # replace a file pikepdf still holds open (WinError 5)
        with pikepdf.open(fixed) as pdf:
            by_ordinal = {o: node for o, node, _pi in walk_figures(pdf)}
            for ordinal, text in pairs:
                node = by_ordinal.get(ordinal)
                if node is None:
                    n_skipped += 1
                    print("WARN: %s no longer has figure #%d - skipped"
                          % (os.path.basename(d), ordinal + 1))
                    continue
                current = _alt_text(node).strip()
                if current not in ("", PLACEHOLDER_ALT):
                    n_skipped += 1
                    print("WARN: %s figure #%d already reads %r - left alone"
                          % (os.path.basename(d), ordinal + 1, current[:60]))
                    continue
                node.Alt = String(text.strip() or DECORATIVE_ALT)
                wrote += 1
            if wrote:
                pdf.save(staged)
        if wrote:
            try:
                shutil.copy2(fixed, fixed + ".prealt")
            except OSError as e:
                print("WARN: could not keep a pre-alt copy of %s (%s) - "
                      "skipping this file rather than writing unverifiably"
                      % (os.path.basename(d), e))
                try:
                    os.remove(staged)
                except OSError:
                    pass
                continue
            atomic_replace(staged, fixed)
            n_alts += wrote
            n_files += 1
    print("alt applied: %d figure(s) across %d file(s)%s"
          % (n_alts, n_files,
             (", %d skipped to protect existing text" % n_skipped)
             if n_skipped else ""))
    return 0

# ----------------------------------------------------- producer-tree repair
# Deterministic fixes for the defects Word/Pearson leave in THEIR tag trees
# and fonts. Census-driven from the 735867 statistics corpus (2026-08-25).
# Every repair is structure-normalizing, never content-changing, and the
# render/text verify gate still judges the result.

def _resolved_resources(page_obj):
    """/Resources with PDF inheritance: climb /Parent when absent. The
    embedder once missed every font on pages using inherited resources -
    they were invisible to a direct .get()."""
    node = page_obj
    for _ in range(32):
        if node is None:
            return None
        r = node.get("/Resources")
        if r is not None:
            return r
        node = node.get("/Parent")
    return None


def _iter_font_dicts(pdf):
    """Every font dict reachable from pages: direct + inherited resources,
    nested Form XObjects, and annotation appearance streams. Yields
    (font_dict, descendant_descriptor_or_None)."""
    seen = set()

    def from_res(res, depth=0):
        if res is None or depth > 6:
            return
        fonts = res.get("/Fonts") or res.get("/Font")
        if fonts is not None:
            for k in fonts.keys():
                try:
                    f = fonts[k]
                    key = tuple(f.objgen) if f.is_indirect else id(f)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield f
                except Exception:
                    continue
        xo = res.get("/XObject")
        if xo is not None:
            for k in xo.keys():
                try:
                    o = xo[k]
                    if o.get("/Subtype") == Name("/Form"):
                        okey = tuple(o.objgen)
                        if okey in seen:
                            continue
                        seen.add(okey)
                        yield from from_res(o.get("/Resources"), depth + 1)
                except Exception:
                    continue

    for page in pdf.pages:
        yield from from_res(_resolved_resources(page.obj))
        for a in (page.obj.get("/Annots") or []):
            try:
                ap = a.get("/AP")
                n = ap.get("/N") if ap is not None else None
                if n is not None and hasattr(n, "get"):
                    yield from from_res(n.get("/Resources"))
            except Exception:
                continue


# minimal Adobe Glyph List subset + patterns; unknown glyphs -> Private Use
_AGL = {
    "space": 0x20, "exclam": 0x21, "quotedbl": 0x22, "numbersign": 0x23,
    "dollar": 0x24, "percent": 0x25, "ampersand": 0x26, "quotesingle": 0x27,
    "parenleft": 0x28, "parenright": 0x29, "asterisk": 0x2A, "plus": 0x2B,
    "comma": 0x2C, "hyphen": 0x2D, "period": 0x2E, "slash": 0x2F,
    "colon": 0x3A, "semicolon": 0x3B, "less": 0x3C, "equal": 0x3D,
    "greater": 0x3E, "question": 0x3F, "at": 0x40, "bracketleft": 0x5B,
    "backslash": 0x5C, "bracketright": 0x5D, "underscore": 0x5F,
    "braceleft": 0x7B, "bar": 0x7C, "braceright": 0x7D, "asciitilde": 0x7E,
    "bullet": 0x2022, "endash": 0x2013, "emdash": 0x2014, "minus": 0x2212,
    "multiply": 0x00D7, "divide": 0x00F7, "plusminus": 0x00B1,
    "degree": 0x00B0, "notequal": 0x2260, "lessequal": 0x2264,
    "greaterequal": 0x2265, "approxequal": 0x2248, "infinity": 0x221E,
    "summation": 0x2211, "product": 0x220F, "radical": 0x221A,
    "integral": 0x222B, "partialdiff": 0x2202, "Delta": 0x0394,
    "Omega": 0x03A9, "Sigma": 0x03A3, "pi": 0x03C0, "mu": 0x03BC,
    "alpha": 0x03B1, "beta": 0x03B2, "gamma": 0x03B3, "delta": 0x03B4,
    "epsilon": 0x03B5, "theta": 0x03B8, "lambda": 0x03BB, "sigma": 0x03C3,
    "phi": 0x03C6, "chi": 0x03C7, "omega": 0x03C9, "nu": 0x03BD,
    "rho": 0x03C1, "tau": 0x03C4, "arrowright": 0x2192, "arrowleft": 0x2190,
    "arrowup": 0x2191, "arrowdown": 0x2193, "element": 0x2208,
    "intersection": 0x2229, "union": 0x222A, "florin": 0x0192,
    "dotlessi": 0x0131, "fraction": 0x2044, "quoteleft": 0x2018,
    "quoteright": 0x2019, "quotedblleft": 0x201C, "quotedblright": 0x201D,
    "ellipsis": 0x2026, "dagger": 0x2020, "daggerdbl": 0x2021,
    "perthousand": 0x2030, "guilsinglleft": 0x2039, "guilsinglright": 0x203A,
    "trademark": 0x2122, "copyright": 0x00A9, "registered": 0x00AE,
    "section": 0x00A7, "paragraph": 0x00B6, "cent": 0x00A2,
    "sterling": 0x00A3, "yen": 0x00A5, "Euro": 0x20AC,
}


def _glyph_to_uni(name):
    n = str(name).lstrip("/")
    m = re.match(r"^uni([0-9A-Fa-f]{4})", n)
    if m:
        return int(m.group(1), 16)
    m = re.match(r"^u([0-9A-Fa-f]{4,6})$", n)
    if m:
        return int(m.group(1), 16)
    if len(n) == 1:
        return ord(n)
    return _AGL.get(n)


def add_tounicode(pdf, notes):
    """PDF/UA 7.21.7: every font must map its character codes. Simple fonts
    without /ToUnicode get a CMap built from their encoding: base encoding
    (WinAnsi via cp1252, MacRoman) overlaid with /Differences glyph names
    (AGL + uniXXXX); unmappable glyphs go to Private Use so the map is
    total and every value is legal (>0)."""
    made = 0
    for f in _iter_font_dicts(pdf):
        try:
            sub = str(f.get("/Subtype"))
            if sub not in ("/Type1", "/TrueType", "/Type3", "/MMType1"):
                continue
            if f.get("/ToUnicode") is not None:
                continue
            enc = f.get("/Encoding")
            base = "cp1252"
            diffs = {}
            if enc is not None and hasattr(enc, "get"):
                if enc.get("/BaseEncoding") == Name("/MacRomanEncoding"):
                    base = "mac_roman"
                d = enc.get("/Differences")
                if d is not None:
                    code = 0
                    for item in d:
                        try:
                            code = int(item)
                        except (TypeError, ValueError):
                            diffs[code] = _glyph_to_uni(item)
                            code += 1
            elif enc == Name("/MacRomanEncoding"):
                base = "mac_roman"
            first = int(f.get("/FirstChar", 32))
            last = int(f.get("/LastChar", 255))
            pairs = []
            for c in range(max(0, first), min(255, last) + 1):
                if c in diffs:
                    u = diffs[c] or (0xE000 + c)
                else:
                    try:
                        u = ord(bytes([c]).decode(base))
                    except Exception:
                        u = 0xE000 + c
                if u <= 0 or u == 0xFEFF or u == 0xFFFE:
                    u = 0xE000 + c
                pairs.append((c, u))
            if not pairs:
                continue
            lines = ["/CIDInit /ProcSet findresource begin",
                     "12 dict begin", "begincmap",
                     "/CIDSystemInfo <</Registry (CF) /Ordering (UCS) /Supplement 0>> def",
                     "/CMapName /CF-UCS def", "/CMapType 2 def",
                     "1 begincodespacerange", "<00> <FF>",
                     "endcodespacerange"]
            for i in range(0, len(pairs), 90):
                chunk = pairs[i:i + 90]
                lines.append("%d beginbfchar" % len(chunk))
                for c, u in chunk:
                    if u > 0xFFFF:
                        u = 0xFFFD
                    lines.append("<%02X> <%04X>" % (c, u))
                lines.append("endbfchar")
            lines += ["endcmap", "CMapName currentdict /CMap defineresource pop",
                      "end", "end"]
            f.ToUnicode = pdf.make_stream(chr(10).join(lines).encode("ascii"))
            made += 1
        except Exception:
            continue
    if made:
        notes.append("built ToUnicode maps for %d font(s)" % made)
    return made


def strip_bad_font_sets(pdf):
    """7.21.4.2: incomplete /CharSet (Type1) and /CIDSet (CID) descriptors
    fail validation; both keys are OPTIONAL, so deletion is the fix."""
    n = 0
    for obj in pdf.objects:
        try:
            if not isinstance(obj, Dictionary):
                continue
            if obj.get("/CharSet") is not None and obj.get("/FontName") is not None:
                del obj["/CharSet"]
                n += 1
            if obj.get("/CIDSet") is not None and obj.get("/FontName") is not None:
                del obj["/CIDSet"]
                n += 1
        except Exception:
            continue
    return n


# ---- structure-tree helpers

def _is_elem(k):
    try:
        return k.get("/S") is not None
    except Exception:
        return False


def _kids(node):
    k = node.get("/K")
    if k is None:
        return []
    if isinstance(k, Array):
        return list(k)
    return [k]


def _set_kids(pdf, node, kids):
    node.K = Array(kids)


def repair_tables(pdf, notes):
    """Normalize Word's malformed table trees:
      - TR children must be TH/TD: stray /Span markers are dropped when
        empty, wrapped in a TD otherwise (7.2-10)
      - orphan TH/TD directly under Table/TBody get a synthesized TR
        (7.2-8/-9); other stray elems become a Caption (first) or a
        single-cell row (7.2-3)
      - tables with NO header row: first row's TDs -> TH Scope=Column (7.5-1)
      - ragged rows padded with empty TDs to the widest row (7.2-43)"""
    fixed = {"span": 0, "wraptr": 0, "caption": 0, "th": 0, "pad": 0}
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return fixed
    ROWGROUPS = (Name("/THead"), Name("/TBody"), Name("/TFoot"))

    # MCIDs that actually paint something. Word gives even EMPTY filler
    # cells real MCIDs, so struct-tree emptiness alone can't identify a
    # placeholder - only a glyphless marked-content region can.
    _vis_cache = {}

    def _page_visible_mcids(pg):
        try:
            key = tuple(pg.objgen)
        except Exception:
            return None
        if key in _vis_cache:
            return _vis_cache[key]
        vis = set()
        try:
            stack = []
            for ins in pikepdf.parse_content_stream(pg):
                op = _op(ins)
                if op == "BDC":
                    m = None
                    try:
                        m = ins.operands[1].get("/MCID")
                    except Exception:
                        m = None
                    stack.append(int(m) if m is not None else None)
                elif op == "BMC":
                    stack.append(None)
                elif op == "EMC":
                    if stack:
                        stack.pop()
                elif op in ("Tj", "'", '"', "TJ"):
                    txt = b""
                    for o in (ins.operands or []):
                        if isinstance(o, String):
                            txt += bytes(o)
                        elif isinstance(o, Array):
                            for x in o:
                                if isinstance(x, String):
                                    txt += bytes(x)
                    if txt.strip(b" \x00\t\r\n"):
                        vis.update(m for m in stack if m is not None)
                elif op in ("Do", "BI", "sh"):
                    vis.update(m for m in stack if m is not None)
        except Exception:
            vis = None          # unparseable: treat all content as visible
        _vis_cache[key] = vis
        return vis

    def _mcid_visible(pg, mcid):
        if pg is None or mcid is None:
            return True         # can't tell -> assume real content
        try:
            mcid = int(mcid)
        except (TypeError, ValueError):
            return True
        v = _page_visible_mcids(pg)
        return True if v is None else mcid in v

    def mk(stype, parent, kids=None):
        d = Dictionary(Type=Name.StructElem, S=Name(stype), P=parent)
        if kids is not None:
            d["/K"] = Array(kids)
        return pdf.make_indirect(d)

    def colspan(cell):
        a = cell.get("/A")
        cands = [a] if a is not None and hasattr(a, "get") else list(a or [])
        for c in cands:
            try:
                v = c.get("/ColSpan")
                if v is not None:
                    return int(v)
            except Exception:
                pass
        return 1

    def fix_tr(tr):
        out = []
        for k in _kids(tr):
            if not _is_elem(k):
                out.append(k)
                continue
            s = k.get("/S")
            if s in (Name("/TH"), Name("/TD")):
                out.append(k)
            elif not _kids(k):          # empty marker (Word's /Span)
                fixed["span"] += 1
            else:                        # content in a wrong role -> TD
                td = mk("/TD", tr, [k])
                k.P = td
                out.append(td)
                fixed["span"] += 1
        _set_kids(pdf, tr, out)

    def fix_table(tbl):
        # 1) normalize direct children
        newkids = []
        pend_cells = []
        first = True

        def flush_cells():
            if pend_cells:
                tr = mk("/TR", tbl, list(pend_cells))
                for c in pend_cells:
                    c.P = tr
                newkids.append(tr)
                fixed["wraptr"] += 1
                del pend_cells[:]

        for k in _kids(tbl):
            if not _is_elem(k):
                continue
            s = k.get("/S")
            if s == Name("/TR"):
                flush_cells()
                fix_tr(k)
                newkids.append(k)
            elif s in ROWGROUPS or s == Name("/Caption"):
                flush_cells()
                for rk in _kids(k):
                    if _is_elem(rk) and rk.get("/S") == Name("/TR"):
                        fix_tr(rk)
                newkids.append(k)
            elif s in (Name("/TH"), Name("/TD")):
                pend_cells.append(k)
            else:                        # stray P/Span at table level
                flush_cells()
                if first:
                    cap = mk("/Caption", tbl, [k])
                    k.P = cap
                    newkids.insert(0, cap)
                    fixed["caption"] += 1
                else:
                    td = mk("/TD", tbl, [k])
                    k.P = td
                    tr = mk("/TR", tbl, [td])
                    td.P = tr
                    newkids.append(tr)
                    fixed["wraptr"] += 1
            first = False
        flush_cells()
        _set_kids(pdf, tbl, newkids)

        # 2) collect all rows in order
        rows = []
        for k in _kids(tbl):
            if not _is_elem(k):
                continue
            if k.get("/S") == Name("/TR"):
                rows.append(k)
            elif k.get("/S") in ROWGROUPS:
                rows += [r for r in _kids(k)
                         if _is_elem(r) and r.get("/S") == Name("/TR")]
        if not rows:
            return
        # 3) header row (7.5-1): no TH anywhere -> first row becomes TH
        has_th = any(_is_elem(c) and c.get("/S") == Name("/TH")
                     for r in rows for c in _kids(r))
        if not has_th:
            for c in _kids(rows[0]):
                if _is_elem(c) and c.get("/S") == Name("/TD"):
                    c.S = Name("/TH")
                    fixed["th"] += 1
        # 3b) EVERY TH needs a Scope (Word writes none): Column when the
        # cell sits in the first row, Row otherwise
        for ri, r in enumerate(rows):
            for c in _kids(r):
                if not (_is_elem(c) and c.get("/S") == Name("/TH")):
                    continue
                a = c.get("/A")
                cands = [a] if a is not None and hasattr(a, "get") else list(a or [])
                if any(x.get("/Scope") is not None for x in cands
                       if hasattr(x, "get")):
                    continue
                scope = Name("/Column") if ri == 0 else Name("/Row")
                if a is None:
                    c.A = Dictionary(O=Name("/Table"), Scope=scope)
                elif hasattr(a, "get") and a.get("/O") == Name("/Table"):
                    a.Scope = scope
                else:
                    c.A = Dictionary(O=Name("/Table"), Scope=scope)
                fixed["th"] += 1
        # 4) ragged rows (7.2-42/-43): simulate the grid INCLUDING RowSpan
        # carryover - a cell spanning down legitimately shortens the rows
        # beneath it, so naive per-row sums over- or under-pad
        def rowspan(cell):
            a = cell.get("/A")
            cands = [a] if a is not None and hasattr(a, "get") else list(a or [])
            for cc in cands:
                try:
                    v = cc.get("/RowSpan")
                    if v is not None:
                        return int(v)
                except Exception:
                    pass
            return 1

        def set_colspan(cell, val):
            a = cell.get("/A")
            cands = ([a] if a is not None and hasattr(a, "get")
                     else list(a or []))
            for cc in cands:
                try:
                    if cc.get("/ColSpan") is not None:
                        if val <= 1:
                            del cc["/ColSpan"]
                        else:
                            cc.ColSpan = val
                        return True
                except Exception:
                    pass
            if val <= 1:
                return True
            attr = Dictionary(O=Name("/Table"), ColSpan=val)
            if a is None:
                cell.A = attr
            elif hasattr(a, "get"):
                cell.A = Array([a, attr])
            else:
                a.append(attr)
            return True

        def _cells(r):
            return [c for c in _kids(r) if _is_elem(c) and
                    c.get("/S") in (Name("/TD"), Name("/TH"))]

        def _has_content(node, pg=None, depth=0):
            # a cell has content only if some marked-content region it
            # references actually PAINTS something - Word's filler cells
            # carry MCIDs for empty paragraphs
            if depth > 20:
                return False
            try:
                pg = node.get("/Pg") or pg
            except Exception:
                pass
            for k in _kids(node):
                try:
                    if _mcid_visible(pg, int(k)):
                        return True
                    continue
                except (TypeError, ValueError):
                    pass
                if not hasattr(k, "get"):
                    continue
                if k.get("/Type") == Name("/OBJR"):
                    return True
                if k.get("/Type") == Name("/MCR"):
                    if _mcid_visible(k.get("/Pg") or pg, k.get("/MCID")):
                        return True
                    continue
                if _is_elem(k) and _has_content(k, pg, depth + 1):
                    return True
            return False

        widths = []
        active = []                      # (rows_remaining, colspan)
        for r in rows:
            inherited = sum(cs for _rl, cs in active)
            active = [(rl - 1, cs) for rl, cs in active if rl - 1 > 0]
            w = inherited
            for c in _kids(r):
                if not _is_elem(c):
                    continue
                cs = colspan(c)
                w += cs
                rs = rowspan(c)
                if rs > 1:
                    active.append((rs - 1, cs))
            widths.append(w)
        if not widths:
            return
        # target = the MODAL width of MULTI-content rows: the real grid is
        # defined by rows that carry several painted cells. Single-content
        # rows are merged bands that must SPAN the grid, so counting them
        # (or trusting the max - Word pads over-wide rows with spurious
        # empty TDs) picks the wrong width.
        ccounts = [sum(1 for c in _cells(r) if _has_content(c))
                   for r in rows]
        counts = {}
        for w, cc in zip(widths, ccounts):
            if cc >= 2:
                counts[w] = counts.get(w, 0) + 1
        if not counts:
            for w in widths:
                counts[w] = counts.get(w, 0) + 1
        target = max(counts, key=lambda w: (counts[w], w))
        for r, w in zip(rows, widths):
            cells = _cells(r)
            content = [c for c in cells if _has_content(c)]
            # Word merged band: a row holding ONE content cell (with or
            # without glyphless placeholder siblings) visually spans the
            # whole grid - padding it with empty TDs equalizes the struct
            # width but still fails veraPDF's geometric check. Span the
            # real cell across instead.
            if len(content) == 1 and w != target:
                empties = {tuple(c.objgen) if c.is_indirect else id(c)
                           for c in cells if not _has_content(c)}
                ks = [k for k in _kids(r)
                      if not (_is_elem(k) and (tuple(k.objgen)
                              if k.is_indirect else id(k)) in empties)]
                _set_kids(pdf, r, ks)
                set_colspan(content[0], target)
                fixed["pad"] += 1
                continue
            if w > target:
                ks = _kids(r)
                for c in reversed(cells):     # drop childless extras first
                    if w <= target:
                        break
                    if not _has_content(c) and (w - colspan(c)) >= target:
                        og = tuple(c.objgen) if c.is_indirect else id(c)
                        ks = [k for k in ks
                              if not (_is_elem(k) and (tuple(k.objgen)
                                      if k.is_indirect else id(k)) == og)]
                        w -= colspan(c)
                        fixed["pad"] += 1
                _set_kids(pdf, r, ks)
                if w > target and cells:      # then clamp the biggest span
                    big = max(cells, key=colspan)
                    cs = colspan(big)
                    if cs > 1 and set_colspan(big, max(1, cs - (w - target))):
                        w -= min(cs - 1, w - target)
                        fixed["pad"] += 1
            for _ in range(target - w):
                td = mk("/TD", r)
                ks = _kids(r)
                ks.append(td)
                _set_kids(pdf, r, ks)
                fixed["pad"] += 1

    seen = set()

    def walk(node, depth):
        if depth > 60 or node is None:
            return
        try:
            if isinstance(node, Array):
                for kid in node:
                    walk(kid, depth + 1)
                return
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if not hasattr(node, "get"):
                return
            if node.get("/S") == Name("/Table"):
                fix_table(node)
            k = node.get("/K")
            if k is not None and not isinstance(k, int):
                walk(k, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), 0)
    tot = sum(fixed.values())
    if tot:
        notes.append("table repair: %d span/rogue cells, %d rows synthesized, "
                     "%d captions, %d header cells, %d padded cells"
                     % (fixed["span"], fixed["wraptr"], fixed["caption"],
                        fixed["th"], fixed["pad"]))
    return fixed


def fix_heading_order(pdf, notes):
    """7.4.2: heading levels must start at H1 and never skip. Walk in
    reading order; demote any heading that jumps more than one level."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    changed = 0
    last = [0]
    seen = set()

    def walk(node, depth):
        nonlocal changed
        if depth > 60 or node is None:
            return
        try:
            if isinstance(node, Array):
                for kid in node:
                    walk(kid, depth + 1)
                return
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if not hasattr(node, "get"):
                return
            s = str(node.get("/S") or "")
            m = re.match(r"^/H([1-6])$", s)
            if m:
                n = int(m.group(1))
                want = min(n, last[0] + 1)
                if want != n:
                    node.S = Name("/H%d" % want)
                    changed += 1
                last[0] = want
            k = node.get("/K")
            if k is not None and not isinstance(k, int):
                walk(k, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), 0)
    if changed:
        notes.append("renumbered %d heading(s) to an unbroken H1.. sequence"
                     % changed)
    return changed


def rewrite_xmp(pdf, title):
    """Replace the XMP packet with the minimal single-Description shape that
    veraPDF verifiably accepts. Producer packets (Word multi-Description +
    extension schemas) parse as valid XML yet are REJECTED whole by
    veraPDF's XMP parser, which then reports dc:title/pdfuaid missing -
    merging into them is a trap; replacement is the fix."""
    esc = (title.replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;"))
    packet = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>' + chr(10) +
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="courseforge">' + chr(10) +
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">' + chr(10) +
        ' <rdf:Description rdf:about="">'
        '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<rdf:Alt><rdf:li xml:lang="x-default">' + esc + '</rdf:li></rdf:Alt>'
        '</dc:title>'
        '<pdfuaid:part xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">1'
        '</pdfuaid:part></rdf:Description></rdf:RDF>' + chr(10) +
        '</x:xmpmeta>' + chr(10) + chr(10) + '<?xpacket end="w"?>' + chr(10))
    st = pdf.make_stream(packet.encode("utf-8"))
    st.Type = Name("/Metadata")
    st.Subtype = Name("/XML")
    pdf.Root.Metadata = st




def normalize_langs(pdf, notes):
    """7.2-29: veraPDF wants canonical BCP-47 case ("en-US", not "EN-US").
    Normalize /Lang on the catalog and every structure element; delete
    empty values."""
    n = 0

    def canon(v):
        # producers append NUL/control bytes ("en-us\x00"); they break both
        # BCP-47 validity and the 2-char-part casing rule below
        t = "".join(ch for ch in str(v)
                    if ch.isprintable()).strip().replace("_", "-")
        if not t:
            return None
        parts = t.split("-")
        out = [parts[0].lower()]
        for p in parts[1:]:
            out.append(p.upper() if len(p) == 2 else p.title()
                       if len(p) == 4 else p.lower())
        return "-".join(out)

    try:
        cl = pdf.Root.get("/Lang")
        if cl is not None:
            c = canon(cl)
            if c is None:
                del pdf.Root["/Lang"]
                n += 1
            elif c != str(cl):
                pdf.Root.Lang = String(c)
                n += 1
    except Exception:
        pass
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return n
    seen = set()

    def walk(node, depth):
        nonlocal n
        if depth > 60 or node is None:
            return
        try:
            if isinstance(node, Array):
                for kid in node:
                    walk(kid, depth + 1)
                return
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if not hasattr(node, "get"):
                return
            lv = node.get("/Lang")
            if lv is not None:
                c = canon(lv)
                if c is None:
                    del node["/Lang"]
                    n += 1
                elif c != str(lv):
                    node.Lang = String(c)
                    n += 1
            k = node.get("/K")
            if k is not None and not isinstance(k, int):
                walk(k, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), 0)
    if n:
        notes.append("normalized %d language tag(s)" % n)
    return n


def augment_cid_tounicode(pdf, notes):
    """7.21.7 on CID fonts: subset math fonts (CambriaMath) ship ToUnicode
    CMaps with GAPS - specific glyphs unmappable. Parse the existing CMap,
    keep every real mapping, and append Private Use mappings for uncovered
    codes so the map is total."""
    patched = 0
    for f in _iter_font_dicts(pdf):
        try:
            sub_t = str(f.get("/Subtype"))
            is_cid = sub_t == "/Type0"
            if not is_cid and sub_t not in ("/TrueType", "/Type1", "/Type3"):
                continue
            tu = f.get("/ToUnicode")
            if tu is None and not is_cid:
                continue          # simple fonts w/o map: add_tounicode's job
            if tu is None:
                # Type0 with NO map at all (subset math fonts): build a
                # total Private-Use map so every used glyph maps (7.21.7)
                lines = ["/CIDInit /ProcSet findresource begin",
                         "12 dict begin", "begincmap",
                         "/CIDSystemInfo <</Registry (CF) /Ordering (UCS) "
                         "/Supplement 0>> def",
                         "/CMapName /CF-UCS2 def", "/CMapType 2 def",
                         "1 begincodespacerange", "<0000> <FFFF>",
                         "endcodespacerange"]
                for i in range(0, 2048, 96):
                    hiN = min(i + 95, 2047)
                    lines.append("1 beginbfrange")
                    lines.append("<%04X> <%04X> <%04X>"
                                 % (i, hiN, 0xE000 + (i % 0x1900)))
                    lines.append("endbfrange")
                lines += ["endcmap",
                          "CMapName currentdict /CMap defineresource pop",
                          "end", "end"]
                f.ToUnicode = pdf.make_stream(
                    chr(10).join(lines).encode("ascii"))
                patched += 1
                continue
            txt = tu.read_bytes().decode("latin-1", "replace")
            # code width: trust the ACTUAL bf-entry sources, not the
            # codespacerange - producers declare <0000><FFFF> and then
            # write 2-hex sources anyway (Canva/InDesign flyers), and a
            # mis-sized parse reads zero coverage, so the PUA fill
            # shadows every real mapping
            srcs2 = len(re.findall(r"(?m)^\s*<[0-9A-Fa-f]{2}>", txt))
            srcs4 = len(re.findall(r"(?m)^\s*<[0-9A-Fa-f]{4}>", txt))
            if srcs2 or srcs4:
                wide = srcs4 > srcs2
            else:
                wide = bool(re.search(
                    r"begincodespacerange\s*<[0-9A-Fa-f]{4}>", txt)) or (
                    is_cid and not re.search(
                        r"begincodespacerange\s*<[0-9A-Fa-f]{2}>", txt))
            W = 4 if wide else 2
            covered = set()
            for block in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
                # dst may be multi-char / surrogate pairs: <0020 00a0>
                for m in re.finditer(
                        r"<([0-9A-Fa-f]{%d})>\s*"
                        r"<[0-9A-Fa-f][0-9A-Fa-f \s]*>" % W, block):
                    covered.add(int(m.group(1), 16))
            for block in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
                for m in re.finditer(
                        r"<([0-9A-Fa-f]{%d})>\s*<([0-9A-Fa-f]{%d})>"
                        % (W, W), block):
                    a, b = int(m.group(1), 16), int(m.group(2), 16)
                    covered.update(range(a, min(b, a + 4096) + 1))
            top = 0xFF if W == 2 else 0xFFFF
            hi = min(top, (max(covered) + 256) if covered else 511)
            if W == 2:
                hi = 0xFF
            missing = [c for c in range(0, hi + 1) if c not in covered]
            if not missing or len(missing) > 20000:
                continue
            add = []
            for i in range(0, len(missing), 90):
                chunk = missing[i:i + 90]
                add.append("%d beginbfchar" % len(chunk))
                for c in chunk:
                    add.append("<%0*X> <%04X>"
                               % (W, c, 0xE000 + (c % 0x1900)))
                add.append("endbfchar")
            if "endcmap" not in txt:
                continue
            newtxt = txt.replace("endcmap",
                                 chr(10).join(add) + chr(10) + "endcmap", 1)
            # invalid destinations count as unmappable too
            newtxt = re.sub(
                r"<([0-9A-Fa-f]{2,4})>(\s*)<(?:0000|FFFE|FEFF)>",
                lambda m: "<%s>%s<%04X>" % (
                    m.group(1), m.group(2),
                    0xE000 + (int(m.group(1), 16) % 0x1900)),
                newtxt)
            f.ToUnicode = pdf.make_stream(newtxt.encode("latin-1"))
            patched += 1
        except Exception:
            continue
    if patched:
        notes.append("filled ToUnicode gaps in %d CID font(s)" % patched)
    return patched




def strip_notdef_glyphs(pdf, notes):
    """7.21.8-1: Word paints character code 0 (the .notdef glyph) as a
    filler. It renders as nothing, but PDF/UA forbids any reference to
    .notdef. Strip zero code units from show strings - single bytes for
    simple fonts, <0000> pairs for Type0."""
    stripped = 0

    def clean(raw, wide):
        """wide is True (Type0, 2-byte codes), False (simple, 1-byte) or None
        when the font in force could not be resolved. None means LEAVE IT:
        stripping single 0x00 bytes out of 2-byte CIDs shifts every following
        code unit and garbles the rest of the run. verify_pair would catch
        that and send the file to fallback, but declining an unprovable edit
        beats earning a fallback."""
        nonlocal stripped
        b = bytes(raw)
        if wide is None:
            return b
        if wide:
            units = [b[i:i + 2] for i in range(0, len(b) - 1, 2)]
            keep = [u for u in units if u != b"\x00\x00"]
            if len(b) % 2:
                keep.append(b[-1:])
            out = b"".join(keep)
        else:
            out = b.replace(b"\x00", b"")
        if out != b:
            stripped += 1
        return out

    def is_wide(fdict):
        try:
            return str(fdict.get("/Subtype")) == "/Type0"
        except Exception:
            return None

    def process(stream_obj, res):
        try:
            instrs = pikepdf.parse_content_stream(stream_obj)
        except Exception:
            return None
        fonts = (res.get("/Font") if res is not None else None) or {}
        cur_wide = None      # None = font unknown -> show strings left alone
        gs_stack = []
        out = []
        changed = False
        for ins in instrs:
            op = _op(ins)
            if op == "q":
                gs_stack.append(cur_wide)
            elif op == "Q":
                if gs_stack:
                    cur_wide = gs_stack.pop()
            elif op == "Tf" and len(ins.operands) >= 1:
                try:
                    cur_wide = is_wide(fonts[str(ins.operands[0])])
                except Exception:
                    cur_wide = None
            elif op in ("Tj", "'", '"'):
                ops = list(ins.operands)
                idx = len(ops) - 1        # string is the last operand
                if isinstance(ops[idx], String) and b"\x00" in bytes(ops[idx]):
                    ops[idx] = String(clean(ops[idx], cur_wide))
                    out.append((ops, Operator(op)))
                    changed = True
                    continue
            elif op == "TJ" and len(ins.operands) == 1 \
                    and isinstance(ins.operands[0], Array):
                if any(isinstance(x, String) and b"\x00" in bytes(x)
                       for x in ins.operands[0]):
                    arr = [String(clean(x, cur_wide))
                           if isinstance(x, String) else x
                           for x in ins.operands[0]]
                    out.append(([Array(arr)], Operator("TJ")))
                    changed = True
                    continue
            out.append(ins)
        return pikepdf.unparse_content_stream(out) if changed else None

    done = set()
    for page in pdf.pages:
        try:
            res = _resolved_resources(page.obj)
            new = process(page, res)
            if new is not None:
                page.Contents = pdf.make_stream(new)
            xo = (res.get("/XObject") if res is not None else None) or {}
            for k in xo.keys():
                try:
                    o = xo[k]
                    if o.get("/Subtype") != Name("/Form"):
                        continue
                    key = tuple(o.objgen)
                    if key in done:
                        continue
                    done.add(key)
                    fres = o.get("/Resources") or res
                    new = process(o, fres)
                    if new is not None:
                        o.write(new)
                except Exception:
                    continue
        except Exception:
            continue
    if stripped:
        notes.append("stripped .notdef (code 0) from %d show op(s)" % stripped)
    return stripped


def fix_struct_parentage(pdf, notes):
    """StructElem /P is REQUIRED; producers (OneNote) omit it on leaf
    elements, and veraPDF then drops the element from the real-content
    chain - every MCID it owns reads as "neither artifact nor tagged"
    (7.1-3). Rewire each child's /P to its actual tree parent."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    fixed = 0
    seen = set()

    def walk(node, parent, depth):
        nonlocal fixed
        if depth > 60 or node is None:
            return
        try:
            if isinstance(node, Array):
                for kid in node:
                    walk(kid, parent, depth + 1)
                return
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
            if not hasattr(node, "get") or node.get("/S") is None:
                return
            p = node.get("/P")
            want = parent
            ok = False
            try:
                ok = (p is not None and p.is_indirect and want.is_indirect
                      and tuple(p.objgen) == tuple(want.objgen))
            except Exception:
                ok = False
            if not ok:
                node.P = want
                fixed += 1
            walk(node.get("/K"), node, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), root, 0)
    if fixed:
        notes.append("rewired %d struct-elem /P parent pointer(s)" % fixed)
    return fixed


def fix_mc_nesting(pdf, notes):
    """7.1-1/-2: producers nest /Artifact regions inside tagged (MCID)
    regions and vice versa. The deterministic repair: DELETE the offending
    artifact begin/end pair, promoting its content into the surrounding
    region - content stays, MCID numbering untouched. An artifact that
    CONTAINS a tagged region loses its wrapper the same way."""
    removed = 0
    for page in pdf.pages:
        try:
            instrs = pikepdf.parse_content_stream(page)
        except Exception:
            continue
        # stack entries: [kind, begin_index]; kinds: 'art' | 'tag' | 'mc'
        stack = []
        drop = set()
        for i, ins in enumerate(instrs):
            op = _op(ins)
            if op in ("BDC", "BMC"):
                tag = str(ins.operands[0]) if len(ins.operands) else ""
                has_mcid = False
                if op == "BDC" and len(ins.operands) > 1:
                    try:
                        has_mcid = ins.operands[1].get("/MCID") is not None
                    except Exception:
                        pass
                kind = ("art" if tag == "/Artifact"
                        else "tag" if has_mcid else "mc")
                if kind == "art" and any(k == "tag" for k, _ in stack):
                    drop.add(i)          # artifact inside tagged content
                    stack.append(["art-drop", i])
                    continue
                if kind == "mc" and tag != "/OC":
                    # struct-orphaned wrapper (/NonStruct, /Div ... with no
                    # MCID): its content is "neither artifact nor tagged"
                    # (7.1-3) even inside a tagged region. Drop the pair -
                    # content inherits the surroundings; /OC stays (it
                    # drives optional-content visibility, not structure)
                    drop.add(i)
                    stack.append(["mc-drop", i])
                    continue
                if kind == "tag":
                    # tagged content inside an artifact: unwrap EVERY
                    # enclosing artifact
                    for ent in stack:
                        if ent[0] == "art":
                            drop.add(ent[1])
                            ent[0] = "art-drop"
                stack.append([kind, i])
            elif op == "EMC":
                if stack:
                    kind, bi = stack.pop()
                    if kind in ("art-drop", "mc-drop"):
                        drop.add(i)
                else:
                    pass                 # unbalanced; leave untouched
        if drop:
            out = [ins for i, ins in enumerate(instrs) if i not in drop]
            page.Contents = pdf.make_stream(
                pikepdf.unparse_content_stream(out))
            removed += len(drop) // 2
    if removed:
        notes.append("unwrapped %d mis-nested artifact region(s)" % removed)
    return removed




def _tree_mcid_roles(pdf):
    """{(page objgen, mcid): S-name} for every MCID the structure tree
    actually references, via K integers (page = elem /Pg) and MCR dicts."""
    out = {}
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return out
    seen = set()

    def walk(node, pg, depth):
        if depth > 60 or node is None:
            return
        try:
            if isinstance(node, Array):
                for kid in node:
                    walk(kid, pg, depth + 1)
                return
            if node.is_indirect:
                key = tuple(node.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if not hasattr(node, "get"):
                return
            s_name = node.get("/S")
            my_pg = node.get("/Pg") or pg
            k = node.get("/K")
            if k is None:
                return
            items = list(k) if isinstance(k, Array) else [k]
            for it in items:
                try:
                    mcid = int(it)
                    if my_pg is not None and s_name is not None:
                        out[(tuple(my_pg.objgen), mcid)] = node
                    continue
                except (TypeError, ValueError):
                    pass
                try:
                    if it.get("/Type") == Name("/MCR"):
                        mpg = it.get("/Pg") or my_pg
                        mc = it.get("/MCID")
                        if mpg is not None and mc is not None:
                            out[(tuple(mpg.objgen), int(mc))] = node
                        continue
                except Exception:
                    pass
                walk(it, my_pg, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), None, 0)
    return out


def fix_artifact_mcid(pdf, notes):
    """The 7.1-1/-2 storm decoded: producers write `BDC /Artifact <</MCID n>>`
    - content marked artifact AND tagged at once, firing both rules per
    item. If the structure tree references the MCID, the content is REAL:
    retag with the owning element's role. If nothing references it, strip
    the MCID and let it be the artifact it claims."""
    roles = _tree_mcid_roles(pdf)
    fixed = 0
    for page in pdf.pages:
        try:
            pg_key = tuple(page.obj.objgen)
            res = _resolved_resources(page.obj)
            propres = res.get("/Properties") if res is not None else None
            instrs = pikepdf.parse_content_stream(page)
            out = []
            changed = False
            for ins in instrs:
                op = _op(ins)
                if op == "BDC" and len(ins.operands) > 1 \
                        and str(ins.operands[0]) == "/Artifact":
                    props = ins.operands[1]
                    mcid = None
                    try:
                        mcid = props.get("/MCID")
                    except Exception:
                        # property list given as a NAME into the page's
                        # /Resources /Properties (Word: "/Artifact /P1 BDC")
                        try:
                            props = propres[str(props)]
                            mcid = props.get("/MCID")
                        except Exception:
                            props = None
                            mcid = None
                    if mcid is not None:
                        elem = roles.get((pg_key, int(mcid)))
                        role = None
                        if elem is not None:
                            try:
                                role = str(elem.get("/S") or "/Span")
                            except Exception:
                                role = "/Span"
                            if role == "/Artifact":
                                # Word wrote an Artifact-TYPED struct elem
                                # referencing this MCID - contradictory on
                                # the tree side too; make both sides /Span
                                try:
                                    elem.S = Name("/Span")
                                except Exception:
                                    pass
                                role = "/Span"
                        if role:                      # real content: retag
                            out.append(([Name(role), props],
                                        Operator("BDC")))
                        else:                          # pure artifact
                            rest = {str(kk).lstrip("/"): props[kk]
                                    for kk in props.keys()
                                    if str(kk) != "/MCID"}
                            if rest:
                                out.append(([Name("/Artifact"),
                                             Dictionary(**rest)],
                                            Operator("BDC")))
                            else:
                                out.append(([Name("/Artifact")],
                                            Operator("BMC")))
                        fixed += 1
                        changed = True
                        continue
                out.append(ins)
            if changed:
                page.Contents = pdf.make_stream(
                    pikepdf.unparse_content_stream(out))
        except Exception as e:
            notes.append("mcid-fix page error (changes on that page kept "
                         "as-is): %r" % e)
            continue
    if fixed:
        notes.append("resolved %d artifact-with-MCID contradiction(s)" % fixed)
    return fixed


def artifact_form_content(pdf, notes):
    """7.1-3 INSIDE Form XObjects: OneNote-style producers fill form
    streams with `/Div <<>> BDC` regions and bare drawing ops, with no
    /StructParents on the stream - that content is neither tagged nor
    artifact no matter how the Do site is marked, and veraPDF descends
    into every form. Convert struct-orphaned BDC wrappers to /Artifact
    BMC and artifact-wrap depth-0 unmarked runs; an MCID the tree really
    references (via /StructParents or an MCR /Stm) is left untouched."""
    stm_refs = set()                    # (form objgen, mcid) in the tree
    root = pdf.Root.get("/StructTreeRoot")
    if root is not None:
        seen = set()

        def collect(node, depth):
            if depth > 60 or node is None:
                return
            try:
                if isinstance(node, Array):
                    for kid in node:
                        collect(kid, depth + 1)
                    return
                if node.is_indirect:
                    key = tuple(node.objgen)
                    if key in seen:
                        return
                    seen.add(key)
                if not hasattr(node, "get"):
                    return
                if node.get("/Type") == Name("/MCR"):
                    stm = node.get("/Stm")
                    mc = node.get("/MCID")
                    if stm is not None and mc is not None:
                        stm_refs.add((tuple(stm.objgen), int(mc)))
                    return
                collect(node.get("/K"), depth + 1)
            except Exception:
                return

        collect(root.get("/K"), 0)

    done = set()
    stats = {"conv": 0, "wrap": 0}

    def transform(form):
        try:
            key = tuple(form.objgen)
        except Exception:
            return
        if key in done:
            return
        done.add(key)
        res = form.get("/Resources")
        xo = res.get("/XObject") if res is not None else None
        if xo is not None:
            for k in xo.keys():
                try:
                    o = xo[k]
                    if o.get("/Subtype") == Name("/Form"):
                        transform(o)
                except Exception:
                    continue
        try:
            instrs = pikepdf.parse_content_stream(form)
        except Exception:
            return
        has_sp = form.get("/StructParents") is not None
        out, buf = [], []
        depth = 0
        changed = False

        def flush():
            nonlocal changed
            if buf:
                out.append(([Name("/Artifact")], Operator("BMC")))
                out.extend(buf)
                out.append(([], Operator("EMC")))
                stats["wrap"] += 1
                changed = True
                del buf[:]

        for ins in instrs:
            d = _mc_delta(ins)
            op = _op(ins)
            if d != 0:
                flush()
                if d > 0 and op in ("BDC", "BMC") \
                        and str(ins.operands[0]) != "/Artifact":
                    mcid = None
                    if op == "BDC" and len(ins.operands) > 1:
                        try:
                            mcid = ins.operands[1].get("/MCID")
                        except Exception:
                            mcid = None
                    tracked = mcid is not None and (
                        has_sp or (key, int(mcid)) in stm_refs)
                    if not tracked:      # struct-orphaned wrapper
                        out.append(([Name("/Artifact")], Operator("BMC")))
                        stats["conv"] += 1
                        changed = True
                        depth += d
                        continue
                out.append(ins)
                depth += d
            elif depth == 0:
                buf.append(ins)
            else:
                out.append(ins)
        flush()
        if changed:
            form.write(pikepdf.unparse_content_stream(out))

    for page in pdf.pages:
        try:
            res = _resolved_resources(page.obj)
            xo = res.get("/XObject") if res is not None else None
            if xo is None:
                continue
            for k in xo.keys():
                try:
                    o = xo[k]
                    if o.get("/Subtype") == Name("/Form"):
                        transform(o)
                except Exception:
                    continue
        except Exception:
            continue
    if stats["conv"] or stats["wrap"]:
        notes.append("form content: %d orphan region(s) -> Artifact, "
                     "%d unmarked run(s) wrapped"
                     % (stats["conv"], stats["wrap"]))
    return stats["conv"] + stats["wrap"]


def artifact_unmarked_pages(pdf, notes):
    """7.1-3 in producer files: page content sitting OUTSIDE any marked
    region is neither tagged nor artifact. Wrap those depth-0 runs in
    /Artifact - MCID numbering is untouched, so this is safe alongside a
    producer tree. Form draws whose (DECODED) content carries marked
    content are emitted unwrapped: tagged content must never sit inside
    an artifact."""
    wrapped = 0
    for page in pdf.pages:
        try:
            res = _resolved_resources(page.obj)
            xo = res.get("/XObject") if res is not None else None
            unsafe = set()
            if xo is not None:
                for k in xo.keys():
                    try:
                        o = xo[k]
                        if o.get("/Subtype") == Name("/Form"):
                            try:
                                data = o.read_bytes()
                            except Exception:
                                unsafe.add(str(k))
                                continue
                            if b"/MCID" in data or b"BDC" in data:
                                unsafe.add(str(k))
                    except Exception:
                        continue
            instrs = pikepdf.parse_content_stream(page)
            out, buf = [], []
            depth = 0
            changed = False

            def flush():
                nonlocal changed, wrapped
                if buf:
                    out.append(([Name("/Artifact")], Operator("BMC")))
                    out.extend(buf)
                    out.append(([], Operator("EMC")))
                    wrapped += 1
                    changed = True
                    del buf[:]

            for ins in instrs:
                d = _mc_delta(ins)
                op = _op(ins)
                if d != 0:
                    flush()
                    out.append(ins)
                    depth += d
                elif depth == 0:
                    if (op == "Do" and len(ins.operands) == 1
                            and str(ins.operands[0]) in unsafe):
                        flush()
                        out.append(ins)
                    else:
                        buf.append(ins)
                else:
                    out.append(ins)
            flush()
            if changed:
                page.Contents = pdf.make_stream(
                    pikepdf.unparse_content_stream(out))
        except Exception:
            continue
    if wrapped:
        notes.append("artifact-wrapped %d unmarked page run(s)" % wrapped)
    return wrapped


def artifact_stray_forms(pdf, notes):
    """7.1-3 in producer files: a Form XObject drawn OUTSIDE any marked
    content (borders/watermarks) leaves its content neither tagged nor
    artifact. Wrapping its Do in /Artifact is safe ONLY when the form
    carries no MCIDs (never nests real content inside an artifact)."""
    wrapped = 0
    for page in pdf.pages:
        try:
            res = _resolved_resources(page.obj)
            xo = res.get("/XObject") if res is not None else None
            if xo is None:
                continue
            safe = set()
            for k in xo.keys():
                try:
                    o = xo[k]
                    if o.get("/Subtype") == Name("/Form"):
                        try:
                            fdata = o.read_bytes()  # DECODED: raw-byte search
                            # once wrapped TAGGED forms in Artifact and lit up
                            # the 7.1-1/-2/-3 trio on three files at once
                        except Exception:
                            continue                # unreadable -> not safe
                        if b"/MCID" not in fdata and b"BDC" not in fdata:
                            safe.add(str(k))
                except Exception:
                    continue
            if not safe:
                continue
            instrs = pikepdf.parse_content_stream(page)
            out = []
            depth = 0
            changed = False
            for ins in instrs:
                d = _mc_delta(ins)
                op = _op(ins)
                if (depth == 0 and d == 0 and op == "Do"
                        and len(ins.operands) == 1
                        and str(ins.operands[0]) in safe):
                    out.append(([Name("/Artifact")], Operator("BMC")))
                    out.append(ins)
                    out.append(([], Operator("EMC")))
                    wrapped += 1
                    changed = True
                else:
                    out.append(ins)
                depth += d
            if changed:
                page.Contents = pdf.make_stream(
                    pikepdf.unparse_content_stream(out))
        except Exception:
            continue
    if wrapped:
        notes.append("artifact-wrapped %d unmarked form draw(s)" % wrapped)
    return wrapped


# --------------------------------------------------------------- verify

def _word_geometry_stable(do, df, out):
    """Discriminates benign font-embed re-rasterization from real damage:
    embedding the same-named font repaints glyphs (pixel drift) but leaves
    word POSITIONS intact; a wrong glyph/width mapping shifts everything
    after it. Requires same words in the same places (p95 shift <= 2pt)."""
    try:
        mismatch = 0
        deltas = []
        seg_pages = 0
        for i in range(min(VERIFY_PAGES, do.page_count, df.page_count)):
            w0 = do[i].get_text("words")
            w1 = df[i].get_text("words")
            if len(w0) != len(w1):
                # embedding a font with different advance widths regroups
                # words ("(" + "2" -> "(2") without touching a single
                # character - compare the raw character stream before
                # calling it damage
                t0 = "".join(c for w in w0 for c in w[4] if c.isalnum())
                t1 = "".join(c for w in w1 for c in w[4] if c.isalnum())
                if t0 and t0 == t1:
                    seg_pages += 1
                    continue
                out["detail"].append("word count changed on page %d (%d -> %d)"
                                     % (i + 1, len(w0), len(w1)))
                return False
            for a, b in zip(w0, w1):
                if a[4] != b[4]:
                    mismatch += 1
                    continue
                deltas.append(max(abs(a[0] - b[0]), abs(a[1] - b[1])))
        if not deltas:
            return seg_pages > 0
        if mismatch > max(2, 0.02 * len(deltas)):
            out["detail"].append("%d word(s) changed text" % mismatch)
            return False
        deltas.sort()
        p95 = deltas[int(0.95 * (len(deltas) - 1))]
        out["word_p95_shift"] = round(p95, 2)
        # 3.5pt: synthesized Widths for bare Base-14 fonts round a hair
        # differently than viewer AFM tables (2.4pt seen live, benign);
        # real corruption accumulates character-widths - tens of points
        if p95 > 3.5:
            out["detail"].append("word positions shifted (p95 %.1fpt)" % p95)
            return False
        return True
    except Exception as e:
        out["detail"].append("geometry check error: %s" % e)
        return False


def verify_pair(orig_path, fixed_path, allow_font_drift=False):
    """Independent check with different libs: pypdf structure + text
    preservation, fitz render smoke test. Returns dict, raises nothing.
    allow_font_drift: when this run embedded fonts, moderate pixel drift
    (>=95% identical) passes IF word geometry is stable - re-rasterization
    moves pixels, corruption moves words."""
    out = {"tags_ok": False, "text_ok": False, "render_ok": False,
           "pages_ok": False, "detail": []}
    try:
        from pypdf import PdfReader
        ro, rf = PdfReader(orig_path), PdfReader(fixed_path)
        out["pages_ok"] = len(ro.pages) == len(rf.pages)
        if not out["pages_ok"]:
            out["detail"].append("page count changed %d -> %d"
                                 % (len(ro.pages), len(rf.pages)))
        root = rf.trailer["/Root"]
        mi = root.get("/MarkInfo")
        out["tags_ok"] = bool(mi and mi.get("/Marked")
                              and root.get("/StructTreeRoot"))
        if not out["tags_ok"]:
            out["detail"].append("fixed file missing MarkInfo/StructTreeRoot")

        def toks(reader):
            # compare alphanumeric content only: glyph-name junk ("/g123")
            # and unmapped-glyph garbage from absent/gapped ToUnicode maps
            # extract as punctuation/PUA noise, and repairing the map
            # legitimately changes that noise on the fixed side
            t = []
            for p in reader.pages:
                try:
                    for w in (p.extract_text() or "").split():
                        if w.startswith("/"):
                            continue
                        w = "".join(c for c in w if c.isalnum())
                        if w:
                            t.append(w)
                except Exception:
                    pass
            return t
        to, tf = toks(ro), set(toks(rf))
        missing = [w for w in to if w not in tf]
        ratio = 1.0 - (len(missing) / len(to)) if to else 1.0
        out["text_ok"] = ratio >= 0.98
        if not out["text_ok"]:
            out["detail"].append("only %.1f%% of original text tokens survive "
                                 "(sample missing: %s)"
                                 % (ratio * 100, missing[:5]))
    except Exception as e:
        out["detail"].append("pypdf verify error: %s" % e)

    try:
        do, df = fitz.open(orig_path), fitz.open(fixed_path)
        match_min = 1.0
        for i in range(min(VERIFY_PAGES, do.page_count, df.page_count)):
            po = do[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            pf = df[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            if (po.width, po.height) != (pf.width, pf.height):
                match_min = 0.0
                out["detail"].append("page %d render size changed" % (i + 1))
                continue
            # byte-identical fast path, else XOR + count at C speed - the
            # original pure-Python zip loop was 67% of ALL pipeline compute
            so, sf = bytes(po.samples), bytes(pf.samples)
            if so == sf:
                frac = 1.0
            else:
                diff = (int.from_bytes(so, "big") ^ int.from_bytes(sf, "big"))
                frac = diff.to_bytes(len(so), "big").count(0) / max(1, len(so))
            match_min = min(match_min, frac)
        out["render_match"] = round(match_min, 4)
        out["render_ok"] = match_min >= RENDER_MATCH_MIN
        # word GEOMETRY is the corruption detector; with it stable, pixel
        # drift is re-rasterization by definition. Text-dense pages with a
        # real font swapped in for a substitute drift ~10% - allow to 0.85.
        if (not out["render_ok"] and allow_font_drift and match_min >= 0.85
                and _word_geometry_stable(do, df, out)):
            out["render_ok"] = True
            out["detail"].append("render drift %.2f%% accepted: font embed "
                                 "re-rasterization, word geometry stable"
                                 % ((1 - match_min) * 100))
        do.close(); df.close()
        if not out["render_ok"]:
            out["detail"].append("render drifted (%.2f%% identical)" % (match_min * 100))
    except Exception as e:
        out["detail"].append("render verify error: %s" % e)
    out["ok"] = all((out["tags_ok"], out["text_ok"], out["render_ok"], out["pages_ok"]))
    return out


# --------------------------------------------------------------- pipeline

def process_one(path, out_path, title=None):
    """The whole fast path for one file. Never raises: returns a result dict
    with status ok | review | fallback | skipped(already-tagged)."""
    t0 = time.perf_counter()
    res = {"file": os.path.basename(path), "path": path, "out": out_path,
           "status": "fallback", "actions": [], "notes": [], "timings": {},
           "confidence": 1.0}

    def clock(stage, start):
        res["timings"][stage] = round(time.perf_counter() - start, 3)

    try:
        t = time.perf_counter()
        info = classify(path)
        res["class_before"] = info.get("cls")
        # carried through so a reader of result.json can say how long the file
        # is without opening it again (the Studio's file table shows pages)
        res["pages"] = info.get("pages")
        clock("classify", t)

        if info.get("cls") == "encrypted":
            res.update(status="fallback", reason="encrypted PDF - needs the password or re-sourcing")
            return res
        if info.get("cls") == "signed":
            res.update(status="fallback", reason="digitally signed - editing invalidates the signature; needs a human decision")
            return res
        if info.get("cls") == "empty/odd":
            res.update(status="fallback", reason=info.get("note", "unreadable/empty PDF"))
            return res

        notes = res["notes"]
        title = title or title_from_filename(path)

        # Already-tagged files (Word exports etc.) keep their producer's tree,
        # but still need title/lang/DisplayDocTitle - the math-course batch
        # showed "missing title" is the DOMINANT finding on exactly these.
        # Same light touch for trees whose producer forgot MarkInfo/Marked.
        if info.get("cls") == "tagged" or _has_struct_tree(path):
            t = time.perf_counter()
            # font embedding is fail-soft: if the embedded version fails
            # verify (Symbol conversions can change extraction), rebuild
            # WITHOUT fonts - a conservative fixed file beats no fix
            # tiers: full repairs -> no CID-gap fill (it can shift text
            # extraction) -> also no Type1 conversion -> no font work at all.
            # Verify gates every tier; first clean build wins.
            for embed_mode, cid_aug in (("all", True), ("all", False),
                                        ("noconv", False), ("none", False)):
                with pikepdf.open(path) as pdf:
                    already_marked = info.get("cls") == "tagged"
                    if not already_marked:
                        pdf.Root.MarkInfo = Dictionary(Marked=True)
                        notes.append("producer wrote a tag tree but no MarkInfo - "
                                     "flag set, existing tree honored (not audited)")
                    ua_harden(pdf, notes=notes)   # Tabs/OC/annot-alt, no graft
                    n_emb = (0 if embed_mode == "none" else
                             embed_missing_fonts(
                                 pdf, notes,
                                 convert_type1=(embed_mode == "all")))
                    n_alt, n_figs_total = alt_existing_figures(pdf, notes)
                    fix_struct_parentage(pdf, notes)
                    repair_tables(pdf, notes)
                    fix_heading_order(pdf, notes)
                    normalize_langs(pdf, notes)
                    add_tounicode(pdf, notes)
                    if cid_aug:
                        augment_cid_tounicode(pdf, notes)
                    strip_bad_font_sets(pdf)
                    strip_notdef_glyphs(pdf, notes)
                    fix_artifact_mcid(pdf, notes)
                    fix_mc_nesting(pdf, notes)
                    artifact_form_content(pdf, notes)
                    artifact_unmarked_pages(pdf, notes)
                    set_metadata(pdf, title)
                    rewrite_xmp(pdf, title)
                    save_pdf(pdf, out_path)
                v_try = verify_pair(path, out_path, allow_font_drift=n_emb > 0)
                if v_try["ok"] or (embed_mode == "none" and not cid_aug):
                    break
                notes.append("repair tier failed verify (%s) - retrying a "
                             "safer tier" % "; ".join(v_try["detail"])[:160])
            v_final = v_try   # loop's last verify IS path->out; never re-verify
            clock("metadata", t)
            # figure pictures are NOT extracted here: collect_alt_todo reads
            # them off the finished fixed.pdf so there is one enumeration and
            # one ordinal for the whole alt pipeline (see figures/alt above)
            res["figures"] = {"total": n_figs_total, "placeholders": n_alt}
            if n_alt:
                res["confidence"] -= min(0.2, 0.02 * n_alt)
            res["actions"].append("metadata-only (existing tag tree kept): "
                                  "title:%r lang:en-US%s%s"
                                  % (title, "" if already_marked else " +Marked",
                                     (", %d figure alt placeholders" % n_alt) if n_alt else ""))
            v = v_final
            res["verify"] = v
            if not v["ok"]:
                res.update(status="fallback",
                           reason="metadata-only verify failed: " + "; ".join(v["detail"]))
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                return res
            res["status"] = "ok"
            return res

        # --- OCR (fitz pass), only when pages lack text
        tmp = out_path + ".ocr.tmp"
        src_for_tags = path
        if info.get("ocr_pages"):
            tess = find_tesseract()
            if not tess:
                res.update(status="fallback",
                           reason="tesseract not installed; %d page(s) need OCR"
                                  % len(info["ocr_pages"]))
                return res
            t = time.perf_counter()
            doc = fitz.open(path)
            words, mean_conf = ocr_pages_inplace(doc, info["ocr_pages"], tess, notes)
            doc.save(tmp, garbage=3, deflate=True)
            doc.close()
            src_for_tags = tmp
            res["actions"].append("ocr:%d pages, %d words, mean conf %.0f"
                                  % (len(info["ocr_pages"]), words, mean_conf))
            if mean_conf and mean_conf < OCR_LOW_MEAN_CONF:
                res["confidence"] -= 0.3
                notes.append("low OCR confidence (%.0f) - text layer may be poor" % mean_conf)
            if not words:
                res["confidence"] -= 0.3
            clock("ocr", t)

        # --- tags + metadata (pikepdf pass); font embedding fail-soft (see
        # the metadata lane): embed, verify, and rebuild without on failure
        t = time.perf_counter()
        v_reuse = None
        for embed_mode in ("all", "noconv", "none"):
            with pikepdf.open(src_for_tags) as pdf:
                n_text, n_fig, n_head, n_art, n_annot, figures = tag_pdf(pdf, notes)
                n_emb = (0 if embed_mode == "none" else
                         embed_missing_fonts(
                             pdf, notes,
                             convert_type1=(embed_mode == "all")))
                add_tounicode(pdf, notes)
                n_aug = 0
                if embed_mode == "all":
                    n_aug = augment_cid_tounicode(pdf, notes)
                strip_bad_font_sets(pdf)
                strip_notdef_glyphs(pdf, notes)
                set_metadata(pdf, title)
                rewrite_xmp(pdf, title)
                save_pdf(pdf, out_path)
            if n_emb == 0 and not n_aug:
                # nothing extraction-risky ran; the outer verify covers it
                break
            v_try = verify_pair(src_for_tags, out_path, allow_font_drift=True)
            if v_try["ok"]:
                # when there was no OCR pass, src_for_tags IS the original,
                # so this verify already proved path->out; reuse it
                if src_for_tags == path:
                    v_reuse = v_try
                break
            notes.append("font embed failed verify (%s) - rebuilt without "
                         "fonts; embedding queued as residual"
                         % "; ".join(v_try["detail"])[:160])
        clock("tag", t)
        res["actions"].append("tags:%d text blocks (%d headings), %d figures, "
                              "%d artifact runs, %d annotations"
                              % (n_text, n_head, n_fig, n_art, n_annot))
        res["actions"].append("title:%r lang:en-US" % title)
        if figures:
            # as in the metadata lane: pictures come from the finished
            # fixed.pdf at collect_alt_todo time, not from here
            res["figures"] = {"total": n_fig, "placeholders": n_fig}
            notes.append("%d figure(s) carry placeholder alt - run the "
                         "description pass before publishing" % n_fig)
            res["confidence"] -= min(0.15, 0.03 * n_fig)
        if n_text > 150:
            notes.append("very fragmented text (%d blocks) - drawing-like layout" % n_text)
            res["confidence"] -= 0.15
        if os.path.exists(tmp):
            os.remove(tmp)

        # --- independent verify
        t = time.perf_counter()
        v = v_reuse if v_reuse is not None else verify_pair(
            path, out_path, allow_font_drift=n_emb > 0)
        res["verify"] = v
        clock("verify", t)
        if not v["ok"]:
            res.update(status="fallback",
                       reason="verify failed: " + "; ".join(v["detail"]))
            try:
                os.remove(out_path)   # never leave an unverified fixed file
            except OSError:
                pass
            return res

        res["status"] = "review" if res["confidence"] < CONFIDENCE_REVIEW else "ok"
        if res["status"] == "review":
            res["reason"] = "fixed and verified, but low confidence - " + "; ".join(notes)
        return res
    except TagAbort as e:
        res.update(status="fallback", reason="tagging refused: %s" % e)
        return res
    except Exception as e:
        res.update(status="fallback",
                   reason="unhandled %s: %s" % (type(e).__name__, e))
        return res
    finally:
        res["timings"]["total"] = round(time.perf_counter() - t0, 3)


def _batch_worker(args):
    path, out_path, title = args
    return process_one(path, out_path, title)


def _queue_entry(r, sub):
    return {"file": r["file"], "dir": sub,
            "severity": "error" if r["status"] == "fallback" else "review",
            "class_before": r.get("class_before"),
            "reason": r.get("reason", ""),
            "notes": r.get("notes", []),
            "hint": ("model: fix this FILE (process_one on a corrected input, "
                     "or manual tooling), and if the same reason repeats "
                     "across files, patch pdf_fastlane.py and re-run "
                     "`selftest` before re-batching")}


# --------------------------------------------------------------- cancelling
# The Studio server runs run_batch on a worker thread and a person may stop
# the job. Killing a batch used to orphan its pool children (28 seen after a
# few kills), which keep handles and CPU. So the live executor is tracked: a
# cancel stops submitting new files and shuts the pool down with
# cancel_futures=True, which is the only way to leave nothing behind.
_CANCEL = threading.Event()
_POOL_LOCK = threading.Lock()
_POOL = None


def cancel_batch():
    """Ask a running run_batch to stop. Safe to call from another thread."""
    _CANCEL.set()
    with _POOL_LOCK:
        pool = _POOL
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


def clear_cancel():
    """Arm a fresh batch. Call before run_batch, not after."""
    _CANCEL.clear()


def batch_cancelled():
    return _CANCEL.is_set()


def _run_tasks(tasks, jobs):
    """process_one over tasks. A worker that dies HARD (mupdf/pikepdf can
    segfault on malformed input, and process_one's except clauses cannot catch
    a dead interpreter) used to take the whole batch's results with it via
    ProcessPoolExecutor.map - 200 fixed files and not one result.json. Submit
    per task instead, and on a broken pool fall back to in-process so the run
    still finishes and the bad file is named."""
    global _POOL
    from concurrent.futures import (BrokenExecutor, CancelledError,
                                    ProcessPoolExecutor)
    if jobs == 1:
        out = []
        for t in tasks:
            if _CANCEL.is_set():
                break
            out.append(_batch_worker(t))
        return out
    results = [None] * len(tasks)
    try:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            with _POOL_LOCK:
                _POOL = ex
            futs = {}
            for i, t in enumerate(tasks):
                if _CANCEL.is_set():
                    break
                futs[ex.submit(_batch_worker, t)] = i
            for fut, i in futs.items():
                try:
                    results[i] = fut.result()
                except CancelledError:
                    continue
                except BrokenExecutor:
                    if _CANCEL.is_set():
                        break
                    raise
                except Exception as e:
                    results[i] = {
                        "file": os.path.basename(tasks[i][0]),
                        "path": tasks[i][0], "out": tasks[i][1],
                        "status": "fallback", "actions": [], "notes": [],
                        "timings": {}, "confidence": 0.0,
                        "reason": "worker crashed on this file (%s: %s)"
                                  % (type(e).__name__, e)}
    except BrokenExecutor as e:
        if _CANCEL.is_set():
            return [r for r in results if r is not None]
        print("  worker pool died (%s) - finishing the remaining files "
              "in-process, one at a time" % e)
        for i, t in enumerate(tasks):
            if results[i] is None:
                try:
                    results[i] = _batch_worker(t)
                except Exception as ex2:
                    results[i] = {
                        "file": os.path.basename(t[0]), "path": t[0],
                        "out": t[1], "status": "fallback", "actions": [],
                        "notes": [], "timings": {}, "confidence": 0.0,
                        "reason": "crashed even single-threaded (%s: %s)"
                                  % (type(ex2).__name__, ex2)}
    finally:
        with _POOL_LOCK:
            _POOL = None
    return [r for r in results if r is not None]


def run_batch(workdir, jobs):
    tasks, cached = [], []
    for d in sorted(os.listdir(workdir)):
        sub = os.path.join(workdir, d)
        orig = os.path.join(sub, "original.pdf")
        if not os.path.isfile(orig):
            continue
        # incremental: a finished file (ok/review/skipped with its artifacts
        # in place) is not redone - delete its result.json (or use the
        # gateway's -Force) to reprocess after a queue fix
        prev = os.path.join(sub, "result.json")
        if os.path.isfile(prev):
            try:
                pr = json.load(open(prev, encoding="utf-8"))
                done = (pr.get("status") == "skipped"
                        or (pr.get("status") in ("ok", "review")
                            and os.path.isfile(os.path.join(sub, "fixed.pdf"))))
                if done:
                    pr["_dir"] = sub
                    cached.append(pr)
                    continue
            except Exception:
                pass
        title = None
        meta = os.path.join(sub, "file.json")
        if os.path.isfile(meta):
            try:
                # utf-8-sig: PS 5.1's Set-Content -Encoding UTF8 writes a BOM,
                # which plain utf-8 json.load rejects (title then silently
                # falls back to "original")
                title = title_from_filename(
                    json.load(open(meta, encoding="utf-8-sig"))["display_name"])
            except Exception:
                pass
        tasks.append((orig, os.path.join(sub, "fixed.pdf"), title))
    if cached and not tasks:
        print("BATCH nothing to do: %d file(s) already processed "
              "(delete result.json or -Force to redo)" % len(cached))
    if not tasks and cached:
        return 0
    if not tasks:
        print("no <id>/original.pdf found under %s" % workdir)
        return 1

    t0 = time.perf_counter()
    jobs = max(1, jobs)
    results = _run_tasks(tasks, jobs)
    wall = time.perf_counter() - t0

    queue = []
    counts = {}
    # CACHED files keep their queue entry. Rebuilding queue.json from this run
    # alone silently emptied it on the second pass, so "N files need a person
    # to look at them" read 0 the moment anything was reprocessed.
    for r in cached:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] in ("fallback", "review"):
            queue.append(_queue_entry(r, r.get("_dir")
                                      or os.path.dirname(r.get("out", ""))))
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        sub = os.path.dirname(r["out"])
        with open(os.path.join(sub, "result.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1)
        if r["status"] in ("fallback", "review"):
            queue.append(_queue_entry(r, sub))
    n_done = len(results)
    summary = {"workdir": os.path.abspath(workdir),
               "files": n_done + len(cached), "processed_now": n_done,
               "cached": len(cached), "wall_seconds": round(wall, 2),
               "per_file_avg_seconds": round(wall / max(1, n_done), 3),
               "jobs": jobs, "status_counts": counts,
               "queued": len(queue),
               "cancelled": bool(_CANCEL.is_set()),
               "not_started": max(0, len(tasks) - n_done)}
    with open(os.path.join(workdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(workdir, "queue.json"), "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=1)

    print("BATCH %d file(s) in %.1fs (%.2fs/file, %d workers)"
          % (n_done, wall, wall / max(1, n_done), jobs))
    if cached:
        print("  cached   %d (already done, kept)" % len(cached))
    for k in ("ok", "review", "skipped", "fallback"):
        if counts.get(k):
            print("  %-8s %d" % (k, counts[k]))
    if queue:
        print("  queue.json -> %d entrie(s) for the model" % len(queue))
    return 0 if not counts.get("fallback") else 2


# -------------------------------------------------------------- veraPDF

def find_verapdf():
    exe = os.environ.get("VERAPDF_BAT")
    if exe and os.path.isfile(exe):
        return exe
    hit = shutil.which("verapdf") or shutil.which("verapdf.bat")
    if hit:
        return hit
    for guess in (r"C:\Program Files\veraPDF\verapdf.bat",
                  os.path.expanduser(r"~\verapdf\verapdf.bat"),
                  os.path.expanduser(r"~\tools\verapdf\verapdf.bat")):
        if os.path.isfile(guess):
            return guess
    # Where the Studio puts it when it installs veraPDF itself: per-user, so no
    # administrator rights are needed. Asked last, and only as a fallback --
    # VERAPDF_BAT above is how the server normally says where it is.
    try:
        from ..verapdf_setup import find as _find
        return _find() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _java_env():
    """verapdf.bat runs %JAVACMD% (default: bare `java`); when java is not on
    PATH, point JAVACMD at a portable JRE (~\\tools\\jdk-*) so no system
    install is required."""
    env = os.environ.copy()
    if env.get("JAVACMD") or shutil.which("java"):
        return env
    import glob as _g
    for j in sorted(_g.glob(os.path.expanduser(r"~\tools\jdk-*\bin\java.exe")),
                    reverse=True):
        env["JAVACMD"] = j
        break
    if not env.get("JAVACMD"):
        # An ordinary system install that simply declined "add to PATH", which
        # is the common case rather than the odd one. Same list the rest of the
        # Studio searches; wrapped because this file also runs as a script.
        try:
            from ..tools import _first_match, java_dirs
            found = _first_match(java_dirs())
            if found:
                env["JAVACMD"] = found
        except Exception:  # noqa: BLE001
            pass
    return env


def run_validate(workdir, profile=None, flavour="ua1"):
    """Validate every fixed.pdf against a REAL standard: PDF/UA-1
    (ISO 14289-1) by default via veraPDF, or any custom veraPDF profile XML
    (e.g. WTPDF 1.0 Accessibility) via --profile. Writes validation.json and
    prints a per-rule failure census. This is the honest scoreboard - scanner
    green is necessary, standard-compliant is the actual bar."""
    vp = find_verapdf()
    if not vp:
        print("veraPDF not found (verapdf.bat). Install it or set VERAPDF_BAT.")
        return 2
    targets = []
    for d in sorted(os.listdir(workdir)):
        f = os.path.join(workdir, d, "fixed.pdf")
        if os.path.isfile(f):
            targets.append(f)
    if not targets:
        print("no fixed.pdf files under %s" % workdir)
        return 1
    # verapdf.bat runs under cmd.exe, which interprets & | < > ^ % ! and quotes
    # inside arguments even when Python passes them as a list. Paths come from
    # the workdir (Canvas ids, a Documents folder); refuse anything odd.
    odd = [t for t in targets + [vp] if re.search(r'[&|<>^%!"\r\n]', t)]
    if odd:
        print("refusing to validate: %d path(s) contain characters cmd.exe would interpret, e.g. %s"
              % (len(odd), odd[0]))
        return 2
    args = [vp, "--format", "json"]
    if profile:
        args += ["--profile", profile]
    else:
        args += ["--flavour", flavour]
    # cmd.exe caps a command line at ~8KB - 80+ absolute paths blows it
    # ("The command line is too long"). Chunk by cumulative path length
    # and merge the report jobs across runs.
    chunks, cur, cur_len = [], [], 0
    for t in targets:
        if cur and cur_len + len(t) > 5500:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(t)
        cur_len += len(t) + 3
    if cur:
        chunks.append(cur)
    t0 = time.perf_counter()
    jobs = []
    for ch in chunks:
        cp = subprocess.run(args + ch, capture_output=True, timeout=3600,
                            env=_java_env(), creationflags=SUBPROC_FLAGS)
        raw = cp.stdout.decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("veraPDF output not parseable (rc=%d): %s" % (cp.returncode, raw[:400]))
            print(cp.stderr.decode("utf-8", "replace")[:400])
            return 2
        jobs.extend(data.get("report", {}).get("jobs", []))
    rules = {}
    passed = failed = 0
    per_file = []
    for j in jobs:
        name = os.path.basename(os.path.dirname(
            j.get("itemDetails", {}).get("name", "?")))
        # veraPDF has shipped validationResult as BOTH a list and a bare
        # dict across versions; indexing [0] on the dict raised KeyError: 0
        vres = j.get("validationResult") or {}
        if isinstance(vres, list):
            vres = vres[0] if vres else {}
        if not isinstance(vres, dict):
            vres = {}
        ok = bool(vres.get("compliant"))
        passed += ok
        failed += (not ok)
        fails = []
        for ra in (vres.get("details", {}).get("ruleSummaries") or []):
            if ra.get("ruleStatus") == "FAILED":
                key = "%s-%s %s" % (ra.get("clause"), ra.get("testNumber"),
                                    (ra.get("description") or "")[:70])
                rules[key] = rules.get(key, 0) + ra.get("failedChecks", 1)
                fails.append(key)
        per_file.append({"dir": name, "compliant": ok, "failed_rules": fails})
    out = {"profile": profile or ("PDF/UA-1 (flavour %s)" % flavour),
           "files": len(jobs), "compliant": passed, "noncompliant": failed,
           "seconds": round(time.perf_counter() - t0, 1),
           "rule_failures": rules, "per_file": per_file}
    with open(os.path.join(workdir, "validation.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("VALIDATE %d file(s) against %s: %d compliant, %d not (%.0fs)"
          % (len(jobs), out["profile"], passed, failed, out["seconds"]))
    for k, v in sorted(rules.items(), key=lambda x: -x[1])[:15]:
        print("  %5d x %s" % (v, k))
    return 0 if failed == 0 else 3


# --------------------------------------------------------------- selftest

def _fixture_text(path):
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 90), "Structural Basics", fontsize=24, fontname="helv")
    for k in range(4):
        pg.insert_text((72, 140 + 20 * k),
                       "Body paragraph line %d about beams and loads." % k,
                       fontsize=11, fontname="helv")
    doc.save(path); doc.close()


def _fixture_image(path):
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), "SCANNED SHEET 42", fontsize=30, fontname="helv")
    pg.insert_text((72, 160), "FOUNDATION DETAIL A", fontsize=18, fontname="helv")
    pix = pg.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    doc.close()
    doc2 = fitz.open()
    pg2 = doc2.new_page()
    pg2.insert_image(pg2.rect, pixmap=pix)
    doc2.save(path); doc2.close()


def _fixture_mixed(path):
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 90), "Mixed Content Page", fontsize=20, fontname="helv")
    pg.insert_text((72, 130), "Some caption text under a small image.",
                   fontsize=11, fontname="helv")
    tiny = fitz.open()
    tp = tiny.new_page(width=100, height=60)
    tp.insert_text((10, 30), "IMG", fontsize=20)
    px = tp.get_pixmap(alpha=False)
    tiny.close()
    pg.insert_image(fitz.Rect(72, 160, 172, 220), pixmap=px)
    doc.save(path); doc.close()


def _fixture_layers(path):
    """CAD-style: text drawn inside an optional-content (/OC) layer, the
    regression case for the 2026-08 blueprint batch (BDC without MCIDs)."""
    doc = fitz.open()
    pg = doc.new_page()
    ocg = doc.add_ocg("Dimensions", on=True)
    pg.insert_text((72, 90), "SHEET 5 FLOOR PLAN", fontsize=22,
                   fontname="helv", oc=ocg)
    pg.insert_text((72, 130), "Scale 1/4 inch = 1 foot", fontsize=11,
                   fontname="helv", oc=ocg)
    doc.save(path); doc.close()


def _fixture_emptyalt(path):
    """Word-style producer tree whose Figure carries /Alt "" - the empty
    string that is not None. Regression for the math-corpus 7.3-1 miss."""
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 90), "Producer Tagged Doc", fontsize=18, fontname="helv")
    tiny = fitz.open()
    tp = tiny.new_page(width=120, height=80)
    tp.insert_text((10, 40), "DIAGRAM", fontsize=16)
    px = tp.get_pixmap(alpha=False)
    tiny.close()
    pg.insert_image(fitz.Rect(72, 120, 192, 200), pixmap=px)
    tmp = path + ".base"
    doc.save(tmp); doc.close()
    with pikepdf.open(tmp) as pdf:
        page = pdf.pages[0]
        st = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        fig = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Figure"), P=st, Pg=page.obj,
            Alt=String("")))                       # <-- the empty alt
        part = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Part"), P=st, K=Array([fig])))
        fig.P = part
        st.K = part
        st.ParentTree = pdf.make_indirect(Dictionary(Nums=Array()))
        pdf.Root.StructTreeRoot = st
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        pdf.save(path)
    os.remove(tmp)


def _fixture_mixed_alt(path):
    """The case that broke the alt pipeline in production: a producer tree with
    TWO figures where the FIRST already carries the author's real alt and the
    second is empty. Three separate index bases disagreed, so the description
    landed on figure 1 (destroying the author's words) while figure 2 kept the
    placeholder. Any regression here is a silent accessibility failure."""
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 90), "Producer Tagged Doc", fontsize=18, fontname="helv")
    for rect, label in ((fitz.Rect(72, 120, 192, 200), "ALPHA"),
                        (fitz.Rect(72, 230, 192, 310), "BETA")):
        t = fitz.open()
        tp = t.new_page(width=120, height=80)
        tp.insert_text((10, 40), label, fontsize=16)
        px = tp.get_pixmap(alpha=False)
        t.close()
        pg.insert_image(rect, pixmap=px)
    tmp = path + ".base"
    doc.save(tmp); doc.close()
    with pikepdf.open(tmp) as pdf:
        page = pdf.pages[0]
        st = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        f0 = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Figure"), P=st, Pg=page.obj,
            Alt=String(AUTHOR_ALT)))
        f1 = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Figure"), P=st, Pg=page.obj,
            Alt=String("")))
        # Word's "alt text" that is really just the source filename: passes
        # 7.3-1, describes nothing, must be reported and NOT overwritten
        f2 = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Figure"), P=st, Pg=page.obj,
            Alt=String("image7.jpeg")))
        part = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Part"), P=st,
            K=Array([f0, f1, f2])))
        f0.P = part; f1.P = part; f2.P = part; st.K = part
        st.ParentTree = pdf.make_indirect(Dictionary(Nums=Array()))
        pdf.Root.StructTreeRoot = st
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        pdf.save(path)
    os.remove(tmp)


def _fixture_vector_figure(path):
    """A Word-shape figure: tagged /Figure, drawn as VECTORS, so it has no
    image XObject at all. page.get_images() returns nothing for it, which used
    to mean 'no picture' and a permanent placeholder under a green report."""
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 90), "Quarterly Enrollment Chart", fontsize=18,
                   fontname="helv")
    pg.insert_text((72, 116), "The chart below shows enrollment by term for "
                             "the last four terms.", fontsize=11,
                   fontname="helv")
    pg.draw_rect(fitz.Rect(90, 140, 300, 300), color=(0, 0, 0), width=1.5)
    for i in range(4):
        pg.draw_rect(fitz.Rect(110 + i * 45, 280 - i * 35, 140 + i * 45, 290),
                     color=(0, 0, 0), fill=(0.2, 0.4, 0.7))
    tmp = path + ".base"
    doc.save(tmp); doc.close()
    with pikepdf.open(tmp) as pdf:
        page = pdf.pages[0]
        st = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        fig = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Figure"), P=st, Pg=page.obj))
        part = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name("/Part"), P=st, K=Array([fig])))
        fig.P = part
        st.K = part
        st.ParentTree = pdf.make_indirect(Dictionary(Nums=Array()))
        pdf.Root.StructTreeRoot = st
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        pdf.save(path)
    os.remove(tmp)


AUTHOR_ALT = "AUTHOR WROTE THIS ALT FOR ALPHA"


def _stage_workdir(td, fixtures):
    """Build a batch workdir: {name: fixture_fn} -> DIR/<id>/original.pdf."""
    wd = os.path.join(td, "work")
    os.makedirs(wd, exist_ok=True)
    for i, (name, make) in enumerate(sorted(fixtures.items())):
        sub = os.path.join(wd, "90%02d" % i)
        os.makedirs(sub, exist_ok=True)
        make(os.path.join(sub, "original.pdf"))
        with open(os.path.join(sub, "file.json"), "w", encoding="utf-8") as f:
            json.dump({"id": 9000 + i, "display_name": name,
                       "folder_id": 1, "size": 1}, f)
    return wd


def _alt_by_ordinal(fixed_pdf):
    with pikepdf.open(fixed_pdf) as p:
        return [_alt_text(n) for _o, n, _pi in walk_figures(p)]


def _selftest_alt_pipeline():
    """run_batch -> collect_alt_todo -> apply_alt, end to end. None of this
    was covered before, and every alt bug we shipped lived in it."""
    ok = True
    with tempfile.TemporaryDirectory() as td:
        wd = _stage_workdir(td, {"mixed-alt.pdf": _fixture_mixed_alt,
                                 "vector.pdf": _fixture_vector_figure})
        run_batch(wd, 1)
        collect_alt_todo(wd, quiet=True)
        todo = json.load(open(os.path.join(wd, "alt-todo.json"),
                              encoding="utf-8"))
        summary = json.load(open(os.path.join(wd, "alt-summary.json"),
                                 encoding="utf-8"))
        # exactly the two undescribed figures are queued; the author's is not
        if summary["needs_alt"] != 2:
            print("FAIL alt pipeline: %d figure(s) queued, expected 2 (%s)"
                  % (summary["needs_alt"], summary))
            ok = False
        if summary["already_described"] != 2:
            print("FAIL alt pipeline: author-described figures not detected "
                  "(%s)" % summary)
            ok = False
        if summary.get("useless_existing_alt") != 1:
            print("FAIL alt pipeline: filename-as-alt ('image7.jpeg') not "
                  "reported (%s)" % summary)
            ok = False
        # the VECTOR figure must have got a real picture, not nothing
        if summary["no_picture_available"] != 0:
            print("FAIL alt pipeline: %d figure(s) had no renderable picture "
                  "- the vector-figure fallback regressed"
                  % summary["no_picture_available"])
            ok = False
        for h, e in todo.items():
            if not (e.get("png") and os.path.isfile(e["png"])):
                print("FAIL alt pipeline: todo %s has no png on disk" % h)
                ok = False
            if e["kind"] not in ("image", "drawing", "page"):
                print("FAIL alt pipeline: todo %s unlabelled kind %r"
                      % (h, e.get("kind")))
                ok = False
        # write descriptions and prove they land on the RIGHT figures
        ap = os.path.join(wd, "alt.json")
        with open(ap, "w", encoding="utf-8") as f:
            json.dump({h: "DESCRIPTION %d" % i
                       for i, h in enumerate(sorted(todo))}, f)
        apply_alt(wd, ap)
        mixed = [d for d in sorted(os.listdir(wd))
                 if os.path.isfile(os.path.join(wd, d, "file.json"))
                 and json.load(open(os.path.join(wd, d, "file.json"),
                                    encoding="utf-8"))["display_name"]
                 == "mixed-alt.pdf"][0]
        alts = _alt_by_ordinal(os.path.join(wd, mixed, "fixed.pdf"))
        if len(alts) != 3:
            print("FAIL alt pipeline: mixed-alt.pdf has %d figures" % len(alts))
            ok = False
        else:
            if alts[0] != AUTHOR_ALT:
                print("FAIL alt pipeline: the author's alt was OVERWRITTEN "
                      "(figure 1 now reads %r)" % alts[0])
                ok = False
            if alts[1].strip() in ("", PLACEHOLDER_ALT):
                print("FAIL alt pipeline: figure 2 still holds the "
                      "placeholder %r - the description went elsewhere"
                      % alts[1])
                ok = False
            if alts[2] != "image7.jpeg":
                print("FAIL alt pipeline: filename-as-alt was overwritten "
                      "(figure 3 now reads %r) - authored text, however bad, "
                      "is not ours to replace" % alts[2])
                ok = False
        # idempotency: a second collect must find NOTHING left to describe
        collect_alt_todo(wd, quiet=True)
        again = json.load(open(os.path.join(wd, "alt-summary.json"),
                               encoding="utf-8"))
        if again["needs_alt"] != 0:
            print("FAIL alt pipeline: re-collect wants to re-describe %d "
                  "figure(s) already done" % again["needs_alt"])
            ok = False
        # queue.json must not lose entries when a second batch runs
        run_batch(wd, 1)
        if not os.path.isfile(os.path.join(wd, "queue.json")):
            print("FAIL alt pipeline: queue.json disappeared on re-batch")
            ok = False
        if ok:
            print("  PASS alt pipeline  right figure, author's alt kept, "
                  "vector figure pictured, idempotent")
    return ok


def selftest():
    ok = True
    if not _selftest_alt_pipeline():
        ok = False
    with tempfile.TemporaryDirectory() as td:
        # empty-alt regression: must be REPAIRED, not skipped
        ea = os.path.join(td, "emptyalt.pdf")
        _fixture_emptyalt(ea)
        r = process_one(ea, os.path.join(td, "fixed_emptyalt.pdf"))
        with pikepdf.open(os.path.join(td, "fixed_emptyalt.pdf")) as p:
            figs = [o for o in p.objects
                    if isinstance(o, Dictionary) and o.get("/S") == Name("/Figure")]
            empties = [f for f in figs if _alt_missing(f)]
        if r["status"] not in ("ok", "review") or empties:
            print("FAIL emptyalt.pdf: status=%s, %d figure(s) still empty-alt"
                  % (r["status"], len(empties)))
            ok = False
        elif not (r.get("figures") or {}).get("placeholders"):
            print("FAIL emptyalt.pdf: repaired figure not queued for alt pass")
            ok = False
        else:
            print("  PASS emptyalt.pdf  empty /Alt repaired + queued")
    with tempfile.TemporaryDirectory() as td:
        cases = {"text.pdf": _fixture_text, "scan.pdf": _fixture_image,
                 "mixed.pdf": _fixture_mixed, "layers.pdf": _fixture_layers}
        for name, make in cases.items():
            make(os.path.join(td, name))
        expect_class = {"text.pdf": "text-untagged", "scan.pdf": "scanned-image",
                        "mixed.pdf": "text-untagged", "layers.pdf": "text-untagged"}
        have_tess = bool(find_tesseract())
        for name in cases:
            src = os.path.join(td, name)
            info = classify(src)
            if info["cls"] != expect_class[name]:
                print("FAIL %s: classified %s, expected %s"
                      % (name, info["cls"], expect_class[name]))
                ok = False
                continue
            # OCR is an optional tool, not a defect. On a machine without
            # tesseract the scanned-image lane cannot run, and reporting that
            # as a regression hides every real failure underneath it. The
            # classification above is still checked; the fix is skipped.
            if name == "scan.pdf" and not have_tess:
                print("  SKIP scan.pdf   class=%s, needs tesseract for the OCR "
                      "lane (not installed here)" % info["cls"])
                continue
            out = os.path.join(td, "fixed_" + name)
            r = process_one(src, out)
            want = "ok"
            if r["status"] != want:
                print("FAIL %s: status %s (%s)" % (name, r["status"], r.get("reason")))
                ok = False
                continue
            after = classify(out)
            if not after["tagged"]:
                print("FAIL %s: output not tagged" % name)
                ok = False
            # every simple font in the output must be embedded (7.21.4.1);
            # the fixtures use fitz's unembedded Base-14 Helvetica, the same
            # shape as Word's unembedded Arial/Times on the math corpus
            with pikepdf.open(out) as chk:
                for pg in chk.pages:
                    fonts = (pg.obj.get("/Resources") or Dictionary()).get("/Font")
                    if fonts is None:
                        continue
                    for fk in fonts.keys():
                        fnt = fonts[fk]
                        fdd = fnt.get("/FontDescriptor")
                        emb = fdd is not None and any(
                            fdd.get(x) is not None
                            for x in ("/FontFile", "/FontFile2", "/FontFile3"))
                        if not emb and str(fnt.get("/Subtype")) in ("/TrueType", "/Type1"):
                            print("FAIL %s: font %s not embedded"
                                  % (name, fnt.get("/BaseFont")))
                            ok = False
            if name == "scan.pdf":
                d = fitz.open(out)
                txt = d[0].get_text("text").upper()
                d.close()
                if "FOUNDATION" not in txt:
                    print("FAIL scan.pdf: OCR text layer missing expected words")
                    ok = False
            # idempotency: re-processing a fixed file must take the light
            # metadata-only lane (existing tree honored), never re-tag
            r2 = process_one(out, os.path.join(td, "twice_" + name))
            acts = " ".join(r2.get("actions", []))
            if r2["status"] != "ok" or "metadata-only" not in acts:
                print("FAIL %s: second pass re-tagged (%s / %s)"
                      % (name, r2["status"], acts))
                ok = False
            print("  %s %-10s class=%s -> tagged, %.2fs"
                  % ("PASS" if ok else "----", name, info["cls"],
                     r["timings"]["total"]))
        # refusal path: encrypted must go to fallback, untouched
        enc = os.path.join(td, "enc.pdf")
        _fixture_text(os.path.join(td, "enc_src.pdf"))
        with pikepdf.open(os.path.join(td, "enc_src.pdf")) as p:
            p.save(enc, encryption=pikepdf.Encryption(owner="x", user="x"))
        r = process_one(enc, os.path.join(td, "fixed_enc.pdf"))
        if r["status"] != "fallback":
            print("FAIL enc.pdf: expected fallback, got %s" % r["status"])
            ok = False
        else:
            print("  PASS enc.pdf    refused -> fallback queue")
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# --------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("pdfs", nargs="+")
    s.add_argument("--json")
    p = sub.add_parser("process"); p.add_argument("pdf")
    p.add_argument("--out", required=True); p.add_argument("--report")
    p.add_argument("--title")
    b = sub.add_parser("batch"); b.add_argument("--workdir", required=True)
    b.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    v = sub.add_parser("verify"); v.add_argument("original"); v.add_argument("fixed")
    g = sub.add_parser("figures"); g.add_argument("--workdir", required=True)
    aa = sub.add_parser("apply-alt"); aa.add_argument("--workdir", required=True)
    aa.add_argument("--alt", required=True)
    vv = sub.add_parser("validate"); vv.add_argument("--workdir", required=True)
    vv.add_argument("--profile"); vv.add_argument("--flavour", default="ua1")
    sub.add_parser("selftest")
    a = ap.parse_args()

    if a.cmd == "scan":
        out = [classify(p) for p in a.pdfs]
        js = json.dumps(out, indent=1)
        if a.json:
            open(a.json, "w", encoding="utf-8").write(js)
        print(js)
        return 0
    if a.cmd == "process":
        r = process_one(a.pdf, a.out, title=a.title)
        js = json.dumps(r, indent=1)
        if a.report:
            open(a.report, "w", encoding="utf-8").write(js)
        print(js)
        return 0 if r["status"] in ("ok", "review", "skipped") else 2
    if a.cmd == "batch":
        return run_batch(a.workdir, a.jobs)
    if a.cmd == "verify":
        v = verify_pair(a.original, a.fixed)
        print(json.dumps(v, indent=1))
        return 0 if v["ok"] else 2
    if a.cmd == "figures":
        return collect_alt_todo(a.workdir)
    if a.cmd == "apply-alt":
        return apply_alt(a.workdir, a.alt)
    if a.cmd == "validate":
        return run_validate(a.workdir, profile=a.profile, flavour=a.flavour)
    if a.cmd == "selftest":
        return selftest()


if __name__ == "__main__":
    sys.exit(main())
