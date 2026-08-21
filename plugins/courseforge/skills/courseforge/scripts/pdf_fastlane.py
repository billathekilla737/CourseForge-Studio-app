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
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import fitz  # PyMuPDF
import pikepdf
from pikepdf import Name, Dictionary, Array, String, Operator

OCR_DPI = 300
OCR_MIN_WORD_CONF = 40.0   # tesseract per-word confidence floor
OCR_LOW_MEAN_CONF = 55.0   # below this the file is flagged for review
HEADING_RATIO = 1.45       # block font >= ratio * body median -> heading
HEADING_MAX_CHARS = 90
CONFIDENCE_REVIEW = 0.60   # below -> "review" queue entry (file still fixed)
RENDER_MATCH_MIN = 0.99    # pixel-identical ratio required on smoke render


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
                capture_output=True, timeout=180)
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
                x, y, w, h = (float(c[6]) / zoom, float(c[7]) / zoom,
                              float(c[8]) / zoom, float(c[9]) / zoom)
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


def save_pdf(pdf, out_path, attempts=5):
    """Save via a temp file then atomic replace, retrying transient Windows
    write locks. On a designer workstation Defender / OneDrive / the search
    indexer routinely hold a freshly written PDF for a second or two, and a
    bare save() then dies with PermissionError - observed live on the math
    corpus. Retrying beats surfacing a phantom failure to the queue."""
    tmp = out_path + ".part"
    last = None
    for i in range(attempts):
        try:
            pdf.save(tmp)
            os.replace(tmp, out_path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.4 * (i + 1))
        except OSError as e:
            last = e
            time.sleep(0.2 * (i + 1))
    for junk in (tmp,):
        try:
            os.remove(junk)
        except OSError:
            pass
    raise PermissionError(
        "could not write %s after %d attempts (file locked by another "
        "process - antivirus/OneDrive/indexer?): %s" % (out_path, attempts, last))


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
                    if b"BT" in xo[k].read_raw_bytes():
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
                    add_elem("/Figure", alt="Graphic in this document")
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


def alt_existing_figures(pdf, notes):
    """Producer trees ship Figure elements with missing or EMPTY /Alt.
    Setting /Alt is a plain key write (no restructuring) so it is safe on a
    foreign tree, but a placeholder only clears the rule - it does not make
    the image accessible. So each one is also RECORDED for the model alt
    pass, paired positionally with the images on its page.
    Returns (n_fixed, [figure records])."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0, []
    page_index = {}
    for i, page in enumerate(pdf.pages):
        try:
            page_index[tuple(page.obj.objgen)] = i
        except Exception:
            pass
    fixed, records, seen = 0, [], set()

    def walk(node, depth):
        nonlocal fixed
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
            if isinstance(node, Array) or isinstance(node, list):
                for kid in node:
                    walk(kid, depth + 1)
                return
            if not hasattr(node, "get"):
                return
            if node.get("/S") == Name("/Figure") and _alt_missing(node):
                node.Alt = String("Graphic in this document")
                pg = node.get("/Pg")
                pi = None
                if pg is not None:
                    try:
                        pi = page_index.get(tuple(pg.objgen))
                    except Exception:
                        pi = None
                records.append({"page": pi, "index": len(records),
                                "positional": True})
                fixed += 1
            k = node.get("/K")
            if k is not None and not isinstance(k, int):
                walk(k, depth + 1)
        except Exception:
            return

    walk(root.get("/K"), 0)
    if fixed:
        notes.append("%d producer-tree figure(s) had missing or EMPTY alt - "
                     "placeholder set to satisfy 7.3-1, queued for a real "
                     "model alt pass (positional pairing)" % fixed)
    return fixed, records


def extract_producer_figures(src_pdf, out_pdf, records, notes):
    """Pair each alt-less producer Figure with an image on its page, in
    order (positional - the skill's documented gallery fallback), so the
    model has something to LOOK at. Figures with no pairable image keep the
    placeholder and are marked unpairable."""
    import hashlib
    fig_dir = os.path.join(os.path.dirname(out_pdf), "figures")
    os.makedirs(fig_dir, exist_ok=True)
    doc = fitz.open(src_pdf)
    by_page = {}
    for r in records:
        by_page.setdefault(r["page"], []).append(r)
    for pi, recs in by_page.items():
        if pi is None or pi >= doc.page_count:
            for r in recs:
                r["png"] = None
            continue
        xrefs = [it[0] for it in doc[pi].get_images(full=True)]
        for slot, r in enumerate(recs):
            if slot >= len(xrefs):
                r["png"] = None
                continue
            try:
                xref = xrefs[slot]
                raw = doc.extract_image(xref)
                r["hash"] = hashlib.md5(raw["image"]).hexdigest()[:16]
                pix = fitz.Pixmap(doc, xref)
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                r["width"], r["height"] = pix.width, pix.height
                png = os.path.join(fig_dir, "prod%03d.png" % r["index"])
                pix.save(png)
                r["png"] = png
            except Exception as e:
                r["png"] = None
                notes.append("producer figure %d: extract failed (%s)"
                             % (r["index"], e))
    doc.close()
    unpaired = sum(1 for r in records if not r.get("png"))
    if unpaired:
        notes.append("%d figure(s) could not be paired with an image - "
                     "placeholder alt stands, needs manual review" % unpaired)


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
        g = cmap.get(code) or sym.get(0xF000 | code) or sym.get(code)
        try:
            widths.append(int(hmtx[g][0] * k) if g else 0)
        except Exception:
            widths.append(0)
    m["widths"] = widths
    tt.close()
    return m


def embed_missing_fonts(pdf, notes):
    """Attach FontFile2 to every unembedded simple font that maps to a local
    Windows font. Existing FontDescriptors just gain /FontFile2; a bare
    standard-14-style font (no descriptor - Word's /Symbol) gets a
    synthesized descriptor + widths and, for TrueType handling, its Subtype
    switched from Type1. Returns fonts embedded; unmapped fonts are noted."""
    cache = {}          # ttf path -> (stream ref, metrics)
    embedded = 0
    unmapped = set()

    def get_stream(path):
        if path not in cache:
            data = open(path, "rb").read()
            st = pdf.make_stream(data)
            st.Length1 = len(data)
            cache[path] = (st, _ttf_metrics(path))
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
        path = _resolve_font_file(f.get("/BaseFont"))
        if not path:
            unmapped.add(str(f.get("/BaseFont")))
            return
        st, m = get_stream(path)
        symbolic = os.path.basename(path).startswith("symbol")
        if fd is None:
            fd = pdf.make_indirect(Dictionary(
                Type=Name.FontDescriptor, FontName=f.get("/BaseFont"),
                Flags=(4 if symbolic else 32), FontBBox=Array(m["bbox"]),
                ItalicAngle=m["italic"], Ascent=m["ascent"],
                Descent=m["descent"], CapHeight=m["cap"], StemV=m["stemv"]))
            f.FontDescriptor = fd
        fd.FontFile2 = st
        if sub == "/Type1":
            # the font program we attach is a TTF; the dict must say so
            f.Subtype = Name("/TrueType")
        if f.get("/Widths") is None:
            f.FirstChar = 32
            f.LastChar = 255
            f.Widths = Array(m["widths"])
        if symbolic and f.get("/Encoding") is not None:
            del f["/Encoding"]        # symbolic TrueType: cmap only
        embedded += 1

    def scan(res):
        fonts = res.get("/Font") if res is not None else None
        if fonts is None:
            return
        for k in fonts.keys():
            try:
                fix_font(fonts[k])
            except Exception:
                continue

    for page in pdf.pages:
        res = page.obj.get("/Resources")
        scan(res)
        xo = res.get("/XObject") if res is not None else None
        if xo is not None:
            for k in xo.keys():
                try:
                    if xo[k].get("/Subtype") == Name("/Form"):
                        scan(xo[k].get("/Resources"))
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

def extract_figures(src_pdf, out_pdf, figures, notes):
    """Save each tagged figure as PNG next to the fixed pdf and stamp a
    content hash, so identical images (logos, repeated diagrams) share one
    alt-text decision across the whole course."""
    import hashlib
    fig_dir = os.path.join(os.path.dirname(out_pdf), "figures")
    os.makedirs(fig_dir, exist_ok=True)
    doc = fitz.open(src_pdf)
    by_page = {}
    for f in figures:
        by_page.setdefault(f["page"], []).append(f)
    for pi, figs in by_page.items():
        name_to_xref = {}
        for item in doc[pi].get_images(full=True):
            name_to_xref["/" + item[7]] = item[0]
        for f in figs:
            xref = name_to_xref.get(f["name"])
            if xref is None:
                f["png"] = None
                continue
            try:
                raw = doc.extract_image(xref)
                f["hash"] = hashlib.md5(raw["image"]).hexdigest()[:16]
                png = os.path.join(fig_dir, "fig%03d.png" % f["index"])
                pix = fitz.Pixmap(doc, xref)
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(png)
                f["png"] = png
            except Exception as e:
                f["png"] = None
                notes.append("figure %d: extract failed (%s)" % (f["index"], e))
    doc.close()


def collect_alt_todo(workdir):
    """Aggregate every figure needing alt across the workdir, deduped by
    image hash -> alt-todo.json. The MODEL then views each listed png and
    writes alt.json {hash: "alt text" | "" for decorative}; apply_alt pushes
    the words into the tag trees."""
    todo = {}
    for d in sorted(os.listdir(workdir)):
        rp = os.path.join(workdir, d, "result.json")
        if not os.path.isfile(rp):
            continue
        r = json.load(open(rp, encoding="utf-8-sig"))
        for f in r.get("figures", []):
            h = f.get("hash")
            if not h or not f.get("png"):
                continue
            e = todo.setdefault(h, {"png": f["png"], "width": f["width"],
                                    "height": f["height"], "used_by": []})
            e["used_by"].append({"dir": os.path.join(workdir, d),
                                 "file": r["file"], "index": f["index"]})
    out = os.path.join(workdir, "alt-todo.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(todo, fh, indent=1)
    print("%d unique image(s) need alt text -> %s" % (len(todo), out))
    for h, e in todo.items():
        print("  %s  %dx%d  used %d time(s)  %s"
              % (h, e["width"], e["height"], len(e["used_by"]), e["png"]))
    return 0


def apply_alt(workdir, alt_path):
    """Write model-authored alt into the fixed PDFs. alt.json maps
    hash -> alt text ('' = decorative; the Figure is demoted to an
    /Artifact-role NonStruct elem is overkill - we keep the Figure and use
    a minimal alt instead, honesty over cleverness)."""
    alt = json.load(open(alt_path, encoding="utf-8-sig"))
    todo_path = os.path.join(workdir, "alt-todo.json")
    todo = json.load(open(todo_path, encoding="utf-8-sig"))
    touched = {}
    for h, text in alt.items():
        if h not in todo:
            print("WARN: hash %s not in alt-todo.json (skipped)" % h)
            continue
        for use in todo[h]["used_by"]:
            touched.setdefault(use["dir"], []).append((use["index"], text))
    n_files = n_alts = 0
    for d, pairs in touched.items():
        fixed = os.path.join(d, "fixed.pdf")
        if not os.path.isfile(fixed):
            print("WARN: no fixed.pdf in %s" % d)
            continue
        with pikepdf.open(fixed, allow_overwriting_input=True) as pdf:
            # collect Figure elements in tree order - works for BOTH lanes
            # (our flat Document tree and a producer's nested tree)
            fig_elems, seen = [], set()

            def collect(node, depth):
                if depth > 60 or node is None:
                    return
                try:
                    if node.is_indirect:
                        k = tuple(node.objgen)
                        if k in seen:
                            return
                        seen.add(k)
                except Exception:
                    pass
                try:
                    if isinstance(node, Array) or isinstance(node, list):
                        for kid in node:
                            collect(kid, depth + 1)
                        return
                    if not hasattr(node, "get"):
                        return
                    if node.get("/S") == Name("/Figure"):
                        fig_elems.append(node)
                    k = node.get("/K")
                    if k is not None and not isinstance(k, int):
                        collect(k, depth + 1)
                except Exception:
                    return

            collect(pdf.Root.StructTreeRoot.get("/K"), 0)
            for index, text in pairs:
                if index < len(fig_elems):
                    fig_elems[index].Alt = String(text or "Decorative graphic")
                    n_alts += 1
            pdf.save(fixed)
        n_files += 1
    print("alt applied: %d figure(s) across %d file(s)" % (n_alts, n_files))
    return 0


# --------------------------------------------------------------- verify

def _word_geometry_stable(do, df, out):
    """Discriminates benign font-embed re-rasterization from real damage:
    embedding the same-named font repaints glyphs (pixel drift) but leaves
    word POSITIONS intact; a wrong glyph/width mapping shifts everything
    after it. Requires same words in the same places (p95 shift <= 2pt)."""
    try:
        mismatch = 0
        deltas = []
        for i in range(min(2, do.page_count, df.page_count)):
            w0 = do[i].get_text("words")
            w1 = df[i].get_text("words")
            if len(w0) != len(w1):
                out["detail"].append("word count changed on page %d (%d -> %d)"
                                     % (i + 1, len(w0), len(w1)))
                return False
            for a, b in zip(w0, w1):
                if a[4] != b[4]:
                    mismatch += 1
                    continue
                deltas.append(max(abs(a[0] - b[0]), abs(a[1] - b[1])))
        if not deltas:
            return False
        if mismatch > max(2, 0.02 * len(deltas)):
            out["detail"].append("%d word(s) changed text" % mismatch)
            return False
        deltas.sort()
        p95 = deltas[int(0.95 * (len(deltas) - 1))]
        out["word_p95_shift"] = round(p95, 2)
        if p95 > 2.0:
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
            t = []
            for p in reader.pages:
                try:
                    t += (p.extract_text() or "").split()
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
        for i in range(min(2, do.page_count, df.page_count)):
            po = do[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            pf = df[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            if (po.width, po.height) != (pf.width, pf.height):
                match_min = 0.0
                out["detail"].append("page %d render size changed" % (i + 1))
                continue
            so, sf = po.samples, pf.samples
            same = sum(a == b for a, b in zip(so, sf))
            match_min = min(match_min, same / max(1, len(so)))
        out["render_match"] = round(match_min, 4)
        out["render_ok"] = match_min >= RENDER_MATCH_MIN
        if (not out["render_ok"] and allow_font_drift and match_min >= 0.95
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
            for try_embed in (True, False):
                with pikepdf.open(path) as pdf:
                    already_marked = info.get("cls") == "tagged"
                    if not already_marked:
                        pdf.Root.MarkInfo = Dictionary(Marked=True)
                        notes.append("producer wrote a tag tree but no MarkInfo - "
                                     "flag set, existing tree honored (not audited)")
                    ua_harden(pdf, notes=notes)   # Tabs/OC/annot-alt, no graft
                    n_emb = embed_missing_fonts(pdf, notes) if try_embed else 0
                    n_alt, prod_figs = alt_existing_figures(pdf, notes)
                    set_metadata(pdf, title)
                    stamp_pdfua_xmp(pdf)
                    save_pdf(pdf, out_path)
                v_try = verify_pair(path, out_path, allow_font_drift=n_emb > 0)
                if v_try["ok"] or not try_embed or n_emb == 0:
                    break
                notes.append("font embed failed verify (%s) - rebuilt without "
                             "fonts; embedding queued as residual"
                             % "; ".join(v_try["detail"])[:160])
            clock("metadata", t)
            if prod_figs:
                extract_producer_figures(path, out_path, prod_figs, notes)
                res["figures"] = prod_figs
                res["confidence"] -= min(0.2, 0.02 * len(prod_figs))
            res["actions"].append("metadata-only (existing tag tree kept): "
                                  "title:%r lang:en-US%s%s"
                                  % (title, "" if already_marked else " +Marked",
                                     (", %d figure alt placeholders" % n_alt) if n_alt else ""))
            v = verify_pair(path, out_path, allow_font_drift=n_emb > 0)
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
        for try_embed in (True, False):
            with pikepdf.open(src_for_tags) as pdf:
                n_text, n_fig, n_head, n_art, n_annot, figures = tag_pdf(pdf, notes)
                n_emb = embed_missing_fonts(pdf, notes) if try_embed else 0
                set_metadata(pdf, title)
                stamp_pdfua_xmp(pdf)
                save_pdf(pdf, out_path)
            if n_emb == 0:
                break
            v_try = verify_pair(src_for_tags, out_path, allow_font_drift=True)
            if v_try["ok"]:
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
            t = time.perf_counter()
            extract_figures(src_for_tags, out_path, figures, notes)
            res["figures"] = figures
            clock("figures", t)
            notes.append("%d figure(s) carry placeholder alt - see figures[] "
                         "for the alt-text pass" % n_fig)
            res["confidence"] -= min(0.15, 0.03 * n_fig)
        if n_text > 150:
            notes.append("very fragmented text (%d blocks) - drawing-like layout" % n_text)
            res["confidence"] -= 0.15
        if os.path.exists(tmp):
            os.remove(tmp)

        # --- independent verify
        t = time.perf_counter()
        v = verify_pair(path, out_path, allow_font_drift=n_emb > 0)
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


def run_batch(workdir, jobs):
    from concurrent.futures import ProcessPoolExecutor
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
    if jobs == 1:
        results = [_batch_worker(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            results = list(ex.map(_batch_worker, tasks))
    wall = time.perf_counter() - t0

    queue = []
    counts = {}
    for r in cached:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        sub = os.path.dirname(r["out"])
        with open(os.path.join(sub, "result.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1)
        if r["status"] in ("fallback", "review"):
            queue.append({"file": r["file"], "dir": sub,
                          "severity": "error" if r["status"] == "fallback" else "review",
                          "class_before": r.get("class_before"),
                          "reason": r.get("reason", ""),
                          "notes": r.get("notes", []),
                          "hint": ("model: fix this FILE (process_one on a corrected "
                                   "input, or manual tooling), and if the same reason "
                                   "repeats across files, patch pdf_fastlane.py and "
                                   "re-run `selftest` before re-batching")})
    summary = {"workdir": os.path.abspath(workdir), "files": len(results),
               "wall_seconds": round(wall, 2),
               "per_file_avg_seconds": round(wall / len(results), 3),
               "jobs": jobs, "status_counts": counts,
               "queued": len(queue)}
    with open(os.path.join(workdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(workdir, "queue.json"), "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=1)

    print("BATCH %d file(s) in %.1fs (%.2fs/file, %d workers)"
          % (len(results), wall, wall / len(results), jobs))
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
    args = [vp, "--format", "json"]
    if profile:
        args += ["--profile", profile]
    else:
        args += ["--flavour", flavour]
    t0 = time.perf_counter()
    cp = subprocess.run(args + targets, capture_output=True, timeout=3600,
                        env=_java_env())
    raw = cp.stdout.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("veraPDF output not parseable (rc=%d): %s" % (cp.returncode, raw[:400]))
        print(cp.stderr.decode("utf-8", "replace")[:400])
        return 2
    jobs = data.get("report", {}).get("jobs", [])
    rules = {}
    passed = failed = 0
    per_file = []
    for j in jobs:
        name = os.path.basename(os.path.dirname(
            j.get("itemDetails", {}).get("name", "?")))
        vres = (j.get("validationResult") or [{}])[0]
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


def selftest():
    ok = True
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
        elif not r.get("figures"):
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
        for name in cases:
            src = os.path.join(td, name)
            info = classify(src)
            if info["cls"] != expect_class[name]:
                print("FAIL %s: classified %s, expected %s"
                      % (name, info["cls"], expect_class[name]))
                ok = False
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
