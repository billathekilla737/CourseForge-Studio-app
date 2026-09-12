"""
remediate_pptx.py (courseforge) - PPTX ADA/Ally remediation engine.

Two-phase design so the AGENT supplies vision (image descriptions, slide titles)
with zero per-file human oversight:

  1) scan   : extract every issue Ally flags on PPTX + dump images to a workdir
              with a manifest the agent reads (it can VIEW the images).
  2) apply  : take the agent-written fixes.json and write a remediated copy:
              - alt text on every picture (descriptive, or "" + decorative flag)
              - slide titles (fill empty placeholder; else clone layout title /
                synthesize one, positioned OFF-CANVAS so visuals are unchanged
                but screen readers announce it - the Microsoft-documented
                "visually hidden title" remediation)
              - table header-row flag (first_row) on tables missing it
  3) verify : re-scan and report what remains (contrast issues are REPORT-ONLY;
              they are design judgments - surface to the instructor).

Usage:
  python -m courseforge.docs.pptx scan   deck.pptx --workdir W
  python -m courseforge.docs.pptx apply  deck.pptx --workdir W --out fixed.pptx
  python -m courseforge.docs.pptx verify fixed.pptx [--original deck.pptx --workdir W]

fixes.json (written by the agent or the Studio into workdir):
  {
    "alts":   { "<imageKey>": "concise description (<=110 chars)" | "" },   # "" = decorative
    "sources": { "<imageKey>": "model" | "human" },                           # who wrote it
    "titles": { "<slideNumber>": "Title text" },
    "table_headers": true
  }

Library surface (what the Studio's gateway calls; nothing here prints):
  scan_report(path, workdir) -> dict          the report, also written to report.json
  apply(path, fixes, out)    -> dict          counts of what was written
  verify(original, fixed, fixes, workdir=None) -> dict   {"ok", "checks", "remaining", ...}
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

from pptx import Presentation
from pptx.util import Emu
from lxml import etree

NS = {
    "a":   "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p":   "http://schemas.openxmlformats.org/presentationml/2006/main",
    "adec": "http://schemas.microsoft.com/office/drawing/2017/decorative",
}
DECOR_URI = "{C183D7F6-B498-43B3-948B-1728B52AA6E4}"
MAX_ALT = 110
REPORT_ONLY = "report-only"


def q(tag):
    pre, local = tag.split(":")
    return "{%s}%s" % (NS[pre], local)


# ---------- traversal ----------

def iter_pictures(shapes):
    for sh in shapes:
        if sh.shape_type == 6:  # GROUP
            yield from iter_pictures(sh.shapes)
        elif sh.shape_type == 13:  # PICTURE
            yield sh


def iter_tables(shapes):
    for sh in shapes:
        if sh.shape_type == 6:
            yield from iter_tables(sh.shapes)
        elif getattr(sh, "has_table", False):
            yield sh


def cnvpr_of(shape):
    el = shape._element
    for child in el.iter():
        if child.tag == q("p:cNvPr") or child.tag.endswith("}cNvPr"):
            return child
    return None


def get_alt(shape):
    c = cnvpr_of(shape)
    return (c.get("descr") or "").strip() if c is not None else ""


def is_decorative(shape):
    c = cnvpr_of(shape)
    if c is None:
        return False
    for ext in c.findall(".//" + q("a:ext")):
        if ext.get("uri") == DECOR_URI:
            for d in ext:
                if d.tag == q("adec:decorative") and d.get("val") in ("1", "true"):
                    return True
    return False


def slide_title_shape(slide):
    try:
        return slide.shapes.title
    except Exception:
        return None


def slide_text_snippet(slide, limit=160):
    return re.sub(r"\s+", " ", " | ".join(slide_texts(slide)))[:limit]


def slide_texts(slide):
    """Every non-empty text frame on the slide, as a list (order kept)."""
    out = []
    for sh in slide.shapes:
        if getattr(sh, "has_text_frame", False):
            t = sh.text_frame.text.strip()
            if t:
                out.append(t)
    return out


FILENAME_RX = re.compile(r"\.(png|jpe?g|gif|bmp|svg|webp|tiff?)\s*$", re.I)


def alt_problem(alt):
    """Why this alt text would be flagged, or None when it is fine."""
    if not alt:
        return "image missing alt"
    if FILENAME_RX.search(alt) or (" " not in alt and "." in alt):
        return "alt is a filename: %r" % alt[:40]
    if len(alt) > MAX_ALT:
        return "alt too long (%d chars)" % len(alt)
    return None


# ---------- contrast (report-only, explicit colors only) ----------

def _lum(rgb):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(fg, bg):
    l1, l2 = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def check_contrast(slide, slide_no, issues):
    for sh in slide.shapes:
        if not getattr(sh, "has_text_frame", False):
            continue
        bg = None
        try:
            if sh.fill.type == 1 and sh.fill.fore_color.type == 1:  # solid, RGB
                c = sh.fill.fore_color.rgb
                bg = (c[0], c[1], c[2])
        except Exception:
            pass
        if bg is None:
            continue  # theme/inherited fills: cannot resolve reliably - skip honestly
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                try:
                    if run.font.color and run.font.color.type == 1:
                        fc = run.font.color.rgb
                        ratio = contrast_ratio((fc[0], fc[1], fc[2]), bg)
                        if ratio < 4.5 and run.text.strip():
                            issues.append({
                                "type": "low-contrast (report-only)",
                                "slide": slide_no,
                                "detail": "text %r ratio %.2f:1 (< 4.5)" % (run.text[:30], ratio),
                            })
                except Exception:
                    pass


# ---------- scan ----------

def is_hard(issue):
    return REPORT_ONLY not in issue.get("type", "")


def scan_report(path, workdir):
    """Scan one deck. Writes images and report.json under workdir, returns the report."""
    prs = Presentation(path)
    os.makedirs(os.path.join(workdir, "images"), exist_ok=True)
    issues, images, untitled, tables = [], [], [], []
    for i, slide in enumerate(prs.slides, start=1):
        for pic in iter_pictures(slide.shapes):
            alt = get_alt(pic)
            key = "s%d_id%d" % (i, pic.shape_id)
            decorative = is_decorative(pic)
            problem = None if decorative else alt_problem(alt)
            ext = pic.image.ext
            blob = pic.image.blob
            img_path = os.path.join(workdir, "images", key + "." + ext)
            with open(img_path, "wb") as f:
                f.write(blob)
            c = cnvpr_of(pic)
            images.append({
                "key": key, "slide": i, "path": img_path, "ext": ext,
                "hash": hashlib.sha1(blob).hexdigest(),
                "current_alt": alt, "decorative": decorative,
                "needs_alt": bool(problem),
                "size_px": [pic.image.size[0], pic.image.size[1]],
                "slide_context": slide_text_snippet(slide),
                "name": (c.get("name") if c is not None else "") or "",
            })
            if problem:
                issues.append({"type": problem.split(":")[0], "slide": i,
                               "detail": problem, "key": key})
        ts = slide_title_shape(slide)
        if ts is None or not ts.text_frame.text.strip():
            issues.append({"type": "missing slide title", "slide": i,
                           "detail": "no title placeholder text"})
            untitled.append({"slide": i, "existing_text": slide_text_snippet(slide),
                             "has_empty_placeholder": ts is not None})
        for t_i, tf in enumerate(iter_tables(slide.shapes)):
            has_header = bool(tf.table.first_row)
            tables.append({"slide": i, "index": t_i, "header_row": has_header,
                           "rows": len(tf.table.rows), "cols": len(tf.table.columns)})
            if not has_header:
                issues.append({"type": "table missing header row", "slide": i,
                               "detail": "first_row flag not set"})
        check_contrast(slide, i, issues)
    hard = [x for x in issues if is_hard(x)]
    report = {"deck": os.path.basename(path), "slides": len(prs.slides),
              "issues": issues, "hard_issues": len(hard),
              "report_only": len(issues) - len(hard),
              "images": images, "untitled": untitled, "tables": tables}
    rp = os.path.join(workdir, "report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return report


def scan(path, workdir):
    """CLI form of scan_report: prints the summary, returns an exit code."""
    report = scan_report(path, workdir)
    print("SCAN %s: %d slides, %d hard issues, %d report-only, %d images extracted"
          % (report["deck"], report["slides"], report["hard_issues"],
             report["report_only"], len(report["images"])))
    for x in report["issues"]:
        print("  [slide %2d] %s - %s" % (x["slide"], x["type"], x["detail"]))
    print("report: %s" % os.path.join(workdir, "report.json"))
    return 0


# ---------- apply ----------

def set_alt(shape, text):
    c = cnvpr_of(shape)
    if c is not None:
        c.set("descr", text)


def set_decorative(shape):
    c = cnvpr_of(shape)
    if c is None:
        return
    c.set("descr", "")
    ext_lst = c.find(q("a:extLst"))
    if ext_lst is None:
        ext_lst = etree.SubElement(c, q("a:extLst"))
    for ext in ext_lst.findall(q("a:ext")):
        if ext.get("uri") == DECOR_URI:
            return
    ext = etree.SubElement(ext_lst, q("a:ext"))
    ext.set("uri", DECOR_URI)
    d = etree.SubElement(ext, q("adec:decorative"))
    d.set("val", "1")


def _next_shape_id(slide):
    ids = [0]
    for el in slide.shapes._spTree.iter():
        if el.tag.endswith("}cNvPr"):
            try:
                ids.append(int(el.get("id") or 0))
            except ValueError:
                pass
    return max(ids) + 1


def ensure_title(slide, prs, text):
    """Fill the empty title placeholder, or clone the layout's, or synthesize one.
    Cloned/synthesized titles are positioned OFF-CANVAS (visuals unchanged,
    screen readers announce them)."""
    ts = slide_title_shape(slide)
    if ts is not None:
        ts.text_frame.text = text
        return "filled placeholder"
    spTree = slide.shapes._spTree
    new_id = _next_shape_id(slide)
    # try cloning the layout's title placeholder so it is a REAL title ph
    layout_title = None
    try:
        layout_title = slide.slide_layout.shapes.title
    except Exception:
        pass
    if layout_title is not None:
        sp = copy.deepcopy(layout_title._element)
        for el in sp.iter():
            if el.tag.endswith("}cNvPr"):
                el.set("id", str(new_id))
                break
        spTree.append(sp)
        slide_w = prs.slide_width
        xml = (
            '<a:xfrm xmlns:a="%s"><a:off x="0" y="-%d"/>'
            '<a:ext cx="%d" cy="%d"/></a:xfrm>'
            % (NS["a"], Emu(914400), slide_w, Emu(457200)))
        spPr = sp.find(".//" + q("p:spPr"))
        if spPr is None:
            spPr = sp.find(".//" + q("a:spPr"))
        if spPr is not None:
            old = spPr.find(q("a:xfrm"))
            if old is not None:
                spPr.remove(old)
            spPr.insert(0, etree.fromstring(xml))
        new_shape = slide.shapes[-1]
        new_shape.text_frame.text = text
        return "cloned layout title (off-canvas)"
    # synthesize a minimal title placeholder sp
    sp_xml = (
        '<p:sp xmlns:p="%s" xmlns:a="%s">'
        '<p:nvSpPr><p:cNvPr id="%d" name="Title"/><p:cNvSpPr/>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="0" y="-914400"/>'
        '<a:ext cx="%d" cy="457200"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody>'
        '</p:sp>' % (NS["p"], NS["a"], new_id, prs.slide_width,
                     text.replace("&", "&amp;").replace("<", "&lt;")))
    spTree.append(etree.fromstring(sp_xml))
    return "synthesized title (off-canvas)"


def clip_alt(val):
    val = (val or "").strip()
    if len(val) > MAX_ALT:
        val = val[:MAX_ALT - 1].rstrip() + "."
    return val


def apply(path, fixes, out):
    """Write a remediated copy of `path` to `out` from a fixes dict. Returns counts."""
    alts = fixes.get("alts", {}) or {}
    titles = {str(k): v for k, v in (fixes.get("titles", {}) or {}).items() if (v or "").strip()}
    fix_tables = fixes.get("table_headers", True)
    prs = Presentation(path)
    n_alt = n_dec = n_title = n_tbl = 0
    title_how = {}
    for i, slide in enumerate(prs.slides, start=1):
        for pic in iter_pictures(slide.shapes):
            key = "s%d_id%d" % (i, pic.shape_id)
            if key in alts and alts[key] is not None:
                val = clip_alt(alts[key])
                if val == "":
                    set_decorative(pic)
                    n_dec += 1
                else:
                    set_alt(pic, val)
                    n_alt += 1
        if str(i) in titles:
            title_how[str(i)] = ensure_title(slide, prs, titles[str(i)].strip())
            n_title += 1
        if fix_tables:
            for tf in iter_tables(slide.shapes):
                if not tf.table.first_row:
                    tf.table.first_row = True
                    n_tbl += 1
    prs.save(out)
    return {"alts": n_alt, "decorative": n_dec, "titles": n_title,
            "table_headers": n_tbl, "title_how": title_how, "out": str(out)}


def apply_fixes(path, workdir, out):
    """CLI form of apply: reads workdir/fixes.json, prints, returns an exit code."""
    fx_path = os.path.join(workdir, "fixes.json")
    with open(fx_path, encoding="utf-8") as f:
        fixes = json.load(f)
    res = apply(path, fixes, out)
    for slide_no, how in res["title_how"].items():
        print("  slide %s title: %s" % (slide_no, how))
    print("APPLY: %d alts, %d decorative, %d titles, %d table headers -> %s"
          % (res["alts"], res["decorative"], res["titles"], res["table_headers"], out))
    return 0


# ---------- verify ----------

def _image_hashes(prs):
    out = []
    for slide in prs.slides:
        for pic in iter_pictures(slide.shapes):
            out.append(hashlib.sha1(pic.image.blob).hexdigest())
    return sorted(out)


def verify(original, fixed, fixes=None, workdir=None):
    """Prove the fixed deck is the original plus the requested fixes, nothing less.

    Checks: the file opens; the slide count is unchanged; every text the original
    showed is still there (titles may be added, nothing removed); the pictures are
    the same set; every alt and title asked for landed. `remaining` lists the hard
    issues a second scan still finds (untitled slides nobody named, and so on);
    they do not fail verify, they are reported so the person can decide.
    """
    fixes = fixes or {}
    checks = {}
    problems = []
    tmp = None
    try:
        try:
            orig = Presentation(original)
            new = Presentation(fixed)
            checks["opens"] = True
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checks": {"opens": False}, "remaining": [],
                    "remaining_hard": None, "report_only": [],
                    "problems": ["the fixed file does not open: %s" % exc]}
        checks["slides_same"] = len(orig.slides) == len(new.slides)
        if not checks["slides_same"]:
            problems.append("slide count changed (%d to %d)" % (len(orig.slides), len(new.slides)))
        lost = []
        for i, (a, b) in enumerate(zip(orig.slides, new.slides), start=1):
            have = set(slide_texts(b))
            for t in slide_texts(a):
                if t not in have:
                    lost.append("slide %d: %r" % (i, t[:60]))
        checks["text_kept"] = not lost
        if lost:
            problems.append("text missing after the fix: " + "; ".join(lost[:5]))
        checks["images_same"] = _image_hashes(orig) == _image_hashes(new)
        if not checks["images_same"]:
            problems.append("the set of pictures changed")

        wanted = {k: clip_alt(v) for k, v in (fixes.get("alts") or {}).items() if v is not None}
        missing_alts = []
        for i, slide in enumerate(new.slides, start=1):
            for pic in iter_pictures(slide.shapes):
                key = "s%d_id%d" % (i, pic.shape_id)
                if key not in wanted:
                    continue
                if wanted[key] == "":
                    if not is_decorative(pic):
                        missing_alts.append(key)
                elif get_alt(pic) != wanted[key]:
                    missing_alts.append(key)
        checks["alts_landed"] = not missing_alts
        if missing_alts:
            problems.append("alt text did not land on: " + ", ".join(missing_alts[:8]))

        titles = {str(k): (v or "").strip() for k, v in (fixes.get("titles") or {}).items()}
        missing_titles = []
        for i, slide in enumerate(new.slides, start=1):
            want = titles.get(str(i))
            if not want:
                continue
            if want not in slide_texts(slide):
                missing_titles.append(str(i))
        checks["titles_landed"] = not missing_titles
        if missing_titles:
            problems.append("titles did not land on slides " + ", ".join(missing_titles))

        if workdir is None:
            tmp = tempfile.mkdtemp(prefix="pptx_verify_")
        report = scan_report(fixed, workdir or tmp)
        remaining = [x for x in report["issues"] if is_hard(x)]
        report_only = [x for x in report["issues"] if not is_hard(x)]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    ok = all(checks.values())
    return {"ok": ok, "checks": checks, "problems": problems,
            "remaining": remaining, "remaining_hard": len(remaining),
            "report_only": report_only}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "apply", "verify"])
    ap.add_argument("deck")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--original", default=None,
                    help="verify: the deck the fixed one was made from")
    a = ap.parse_args()
    if a.cmd == "scan":
        return scan(a.deck, a.workdir or a.deck + ".work")
    if a.cmd == "apply":
        if not a.out:
            ap.error("--out required for apply")
        return apply_fixes(a.deck, a.workdir or a.deck + ".work", a.out)
    if a.original:
        fixes = {}
        fx = os.path.join(a.workdir or a.original + ".work", "fixes.json")
        if os.path.isfile(fx):
            with open(fx, encoding="utf-8") as f:
                fixes = json.load(f)
        res = verify(a.original, a.deck, fixes)
        print("VERIFY %s: %s" % (os.path.basename(a.deck), "ok" if res["ok"] else "FAILED"))
        for k, v in res["checks"].items():
            print("  %-14s %s" % (k, "yes" if v else "NO"))
        for p in res["problems"]:
            print("  problem: %s" % p)
        print("  %d hard issue(s) remain" % res["remaining_hard"])
        return 0 if res["ok"] else 2
    tmp = None if a.workdir else tempfile.mkdtemp(prefix="pptx_verify_")
    try:
        return scan(a.deck, a.workdir or tmp)
    finally:
        if tmp:      # every image of the deck was extracted here
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
