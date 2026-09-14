"""
remediate_docx.py (courseforge) - Word (.docx) ADA/Ally remediation engine.
Mirror of remediate_pptx.py: two-phase so the AGENT supplies vision (image
descriptions) and judgment (which paragraphs are really headings) with no
per-file human oversight.

  1) scan   : find the issues Ally flags on DOCX - images with missing/filename
              alt, substantial documents with no real Heading styles, heading
              level skips, tables without a repeating header row - and extract
              every image to a workdir with a manifest the agent reads.
  2) apply  : take the agent-written fixes.json and write a remediated copy:
              - alt text on every image ("" = decorative is NOT supported by
                Word the way PPTX does it, so decorative images get alt "" only)
              - optional paragraph -> Heading style promotion (agent-mapped;
                changes appearance, so it is OPT-IN via fixes.json, never auto)
              - first-row header flag (w:tblHeader) on tables missing it
  3) verify : re-scan; hard issues should be 0 (un-promoted heading candidates
              downgrade to advisory when fixes.json chose not to promote).

HONEST SCOPE: alt text + table header rows + opt-in heading promotion. This is
NOT full document tagging; reading order / contrast / list semantics stay the
author's job. python-docx has no first-class alt API - we edit the drawing XML
(wp:docPr @descr) directly.

Usage:
  python -m courseforge.docs.docx scan   doc.docx --workdir W
  python -m courseforge.docs.docx apply  doc.docx --workdir W --out fixed.docx
  python -m courseforge.docs.docx verify fixed.docx [--original doc.docx --workdir W]

fixes.json (agent-written or Studio-written, in workdir):
  {
    "alts":     { "<imageKey>": "concise description (<=110 chars)" | "" },
    "sources":  { "<imageKey>": "model" | "human" },
    "headings": { "<paragraphIndex>": 1|2|3 },     # opt-in style promotion
    "table_headers": true
  }

Library surface (what the Studio's gateway calls; nothing here prints):
  scan_report(path, workdir) -> dict
  apply(path, fixes, out)    -> dict
  verify(original, fixed, fixes, workdir=None) -> dict
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

from docx import Document
from docx.oxml.ns import qn

MAX_ALT = 110
FILENAME_RX = re.compile(r"\.(png|jpe?g|gif|bmp|svg|webp|tiff?)\s*$", re.I)


# ---------- drawing helpers (alt text lives on wp:docPr @descr) ----------

def iter_drawings(doc):
    """Yield (index, docPr element, embedded image part or None) for every
    inline/floating drawing in body order."""
    body = doc.element.body
    idx = 0
    for docPr in body.iter(qn("wp:docPr")):
        # find the blip (image relationship) inside the same drawing, if any.
        # Located from docPr's own parent (inline/anchor), so the grandparent
        # <drawing> is never needed - it used to be bound here and unused.
        blip = None
        anc = docPr.getparent()
        if anc is not None:
            blip = anc.find(".//" + qn("a:blip"))
        yield idx, docPr, blip
        idx += 1


def get_image_blob(doc, blip):
    if blip is None:
        return None, None
    rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
    if not rid:
        return None, None
    try:
        part = doc.part.related_parts[rid]
        ext = part.partname.ext.lstrip(".")
        return part.blob, ext
    except Exception:
        return None, None


def alt_problem(alt):
    if not alt:
        return "image missing alt"
    if FILENAME_RX.search(alt) or (" " not in alt and "." in alt):
        return "alt is a filename: %r" % alt[:40]
    if len(alt) > MAX_ALT:
        return "alt too long (%d chars)" % len(alt)
    return None


# ---------- headings ----------

def heading_level(par):
    name = (par.style.name or "") if par.style is not None else ""
    m = re.match(r"heading (\d+)", name, re.I)
    return int(m.group(1)) if m else None


def looks_like_faux_heading(par):
    """Short, bold-run paragraph with no heading style = probable faux heading."""
    text = par.text.strip()
    if not text or len(text) > 80 or heading_level(par) is not None:
        return False
    runs = [r for r in par.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs)


def _context(paras, i, span=1):
    """A little of the text around paragraph i, for the review table."""
    before = " ".join(p.text.strip() for p in paras[max(0, i - span):i] if p.text.strip())
    after = " ".join(p.text.strip() for p in paras[i + 1:i + 1 + span] if p.text.strip())
    return {"before": before[-120:], "after": after[:120]}


# ---------- tables ----------

def table_has_header_row(table):
    if not table.rows:
        return False
    trPr = table.rows[0]._tr.find(qn("w:trPr"))
    return trPr is not None and trPr.find(qn("w:tblHeader")) is not None


def set_table_header_row(table):
    tr = table.rows[0]._tr
    trPr = tr.find(qn("w:trPr"))
    if trPr is None:
        trPr = tr.makeelement(qn("w:trPr"), {})
        tr.insert(0, trPr)
    if trPr.find(qn("w:tblHeader")) is None:
        trPr.append(tr.makeelement(qn("w:tblHeader"), {}))


def _table_preview(table):
    try:
        return [c.text.strip()[:30] for c in table.rows[0].cells][:6]
    except Exception:
        return []


# ---------- scan ----------

def scan_report(path, workdir):
    """Scan one document. Writes images and report.json under workdir, returns the report."""
    doc = Document(path)
    os.makedirs(os.path.join(workdir, "images"), exist_ok=True)
    issues, images, faux, tables = [], [], [], []

    for idx, docPr, blip in iter_drawings(doc):
        alt = (docPr.get("descr") or "").strip()
        key = "img%d" % idx
        blob, ext = get_image_blob(doc, blip)
        img_path = None
        digest = None
        if blob:
            img_path = os.path.join(workdir, "images", "%s.%s" % (key, ext or "png"))
            with open(img_path, "wb") as f:
                f.write(blob)
            digest = hashlib.sha1(blob).hexdigest()
        problem = alt_problem(alt)
        images.append({"key": key, "path": img_path, "ext": ext or "", "hash": digest,
                       "current_alt": alt, "needs_alt": bool(problem),
                       "decorative": False, "name": docPr.get("name") or ""})
        if problem:
            issues.append({"type": problem.split(":")[0], "detail": problem, "key": key})

    paras = doc.paragraphs
    total_text = sum(len(p.text.strip()) for p in paras)
    levels = [heading_level(p) for p in paras if heading_level(p)]
    if total_text > 600 and not levels:
        issues.append({"type": "no real headings",
                       "detail": "document has %d chars of text and zero Heading styles" % total_text})
    prev = 0
    for lv in levels:
        if prev and lv > prev + 1:
            issues.append({"type": "skipped heading level",
                           "detail": "H%d follows H%d" % (lv, prev)})
        prev = lv
    for i, p in enumerate(paras):
        if looks_like_faux_heading(p):
            faux.append({"index": i, "text": p.text.strip()[:70], **_context(paras, i)})

    for t_i, table in enumerate(doc.tables):
        has_header = table_has_header_row(table)
        tables.append({"index": t_i, "header_row": has_header, "rows": len(table.rows),
                       "cols": len(table.columns), "first_row": _table_preview(table)})
        if not has_header:
            issues.append({"type": "table missing header row",
                           "detail": "table %d: first row not marked w:tblHeader" % t_i})

    report = {"doc": os.path.basename(path), "paragraphs": len(paras),
              "issues": issues, "hard_issues": len(issues), "report_only": 0,
              "images": images, "faux_heading_candidates": faux, "tables": tables}
    rp = os.path.join(workdir, "report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return report


def scan(path, workdir):
    """CLI form of scan_report: prints the summary, returns an exit code."""
    report = scan_report(path, workdir)
    print("SCAN %s: %d issues, %d image(s), %d faux-heading candidate(s)"
          % (report["doc"], len(report["issues"]), len(report["images"]),
             len(report["faux_heading_candidates"])))
    for x in report["issues"]:
        print("  %s - %s" % (x["type"], x["detail"]))
    for c in report["faux_heading_candidates"]:
        print("  [candidate heading] par %d: %r" % (c["index"], c["text"]))
    print("report: %s" % os.path.join(workdir, "report.json"))
    return 0


# ---------- apply ----------

def clip_alt(val):
    val = (val or "").strip()
    if len(val) > MAX_ALT:
        val = val[:MAX_ALT - 1].rstrip() + "."
    return val


def apply(path, fixes, out):
    """Write a remediated copy of `path` to `out` from a fixes dict. Returns counts."""
    alts = fixes.get("alts", {}) or {}
    headings = {}
    for k, v in (fixes.get("headings", {}) or {}).items():
        try:
            if v not in (None, "", 0, "0"):
                headings[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    fix_tables = fixes.get("table_headers", True)

    doc = Document(path)
    n_alt = n_head = n_tbl = 0
    for idx, docPr, _blip in iter_drawings(doc):
        key = "img%d" % idx
        if key in alts and alts[key] is not None:
            docPr.set("descr", clip_alt(alts[key]))
            n_alt += 1
    for i, lv in headings.items():
        if 0 <= i < len(doc.paragraphs) and 1 <= lv <= 4:
            doc.paragraphs[i].style = doc.styles["Heading %d" % lv]
            n_head += 1
    if fix_tables:
        for table in doc.tables:
            if not table_has_header_row(table):
                set_table_header_row(table)
                n_tbl += 1
    doc.save(out)
    return {"alts": n_alt, "headings": n_head, "table_headers": n_tbl, "out": str(out)}


def apply_fixes(path, workdir, out):
    """CLI form of apply: reads workdir/fixes.json, prints, returns an exit code."""
    with open(os.path.join(workdir, "fixes.json"), encoding="utf-8") as f:
        fixes = json.load(f)
    res = apply(path, fixes, out)
    print("APPLY: %d alt(s), %d heading promotion(s), %d table header row(s) -> %s"
          % (res["alts"], res["headings"], res["table_headers"], out))
    return 0


# ---------- verify ----------

def _image_hashes(doc):
    out = []
    for _idx, _docPr, blip in iter_drawings(doc):
        blob, _ext = get_image_blob(doc, blip)
        if blob:
            out.append(hashlib.sha1(blob).hexdigest())
    return sorted(out)


def verify(original, fixed, fixes=None, workdir=None):
    """Prove the fixed document is the original plus the requested fixes.

    Checks: opens; the paragraph text is identical, paragraph for paragraph (a
    heading promotion changes a style, never a word); the tables and pictures
    are the same; every alt asked for landed; every promotion asked for landed.
    `remaining` lists what a second scan still flags; it is reported, not fatal.
    """
    fixes = fixes or {}
    checks = {}
    problems = []
    tmp = None
    try:
        try:
            orig = Document(original)
            new = Document(fixed)
            checks["opens"] = True
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checks": {"opens": False}, "remaining": [],
                    "remaining_hard": None, "report_only": [],
                    "problems": ["the fixed file does not open: %s" % exc]}
        a = [p.text for p in orig.paragraphs]
        b = [p.text for p in new.paragraphs]
        checks["text_identical"] = a == b
        if a != b:
            problems.append("paragraph text differs after the fix")
        checks["tables_same"] = len(orig.tables) == len(new.tables) and all(
            [[c.text for c in r.cells] for r in t1.rows] == [[c.text for c in r.cells] for r in t2.rows]
            for t1, t2 in zip(orig.tables, new.tables))
        if not checks["tables_same"]:
            problems.append("table contents differ after the fix")
        checks["images_same"] = _image_hashes(orig) == _image_hashes(new)
        if not checks["images_same"]:
            problems.append("the set of pictures changed")

        wanted = {k: clip_alt(v) for k, v in (fixes.get("alts") or {}).items() if v is not None}
        missing = []
        for idx, docPr, _blip in iter_drawings(new):
            key = "img%d" % idx
            if key in wanted and (docPr.get("descr") or "").strip() != wanted[key]:
                missing.append(key)
        checks["alts_landed"] = not missing
        if missing:
            problems.append("alt text did not land on: " + ", ".join(missing[:8]))

        bad_heads = []
        for k, v in (fixes.get("headings") or {}).items():
            try:
                i, lv = int(k), int(v)
            except (TypeError, ValueError):
                continue
            if lv < 1 or i < 0 or i >= len(new.paragraphs):
                continue
            if heading_level(new.paragraphs[i]) != lv:
                bad_heads.append(str(i))
        checks["headings_landed"] = not bad_heads
        if bad_heads:
            problems.append("heading promotions did not land on paragraphs " + ", ".join(bad_heads))

        if workdir is None:
            tmp = tempfile.mkdtemp(prefix="docx_verify_")
        report = scan_report(fixed, workdir or tmp)
        remaining = list(report["issues"])
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if remaining:
        problems.append("%d Ally issue(s) still present" % len(remaining))
    return {"ok": all(checks.values()) and not remaining, "checks": checks,
            "problems": problems, "remaining": remaining,
            "remaining_hard": len(remaining), "report_only": []}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "apply", "verify"])
    ap.add_argument("doc")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--original", default=None,
                    help="verify: the document the fixed one was made from")
    a = ap.parse_args()
    if a.cmd == "scan":
        return scan(a.doc, a.workdir or a.doc + ".work")
    if a.cmd == "apply":
        if not a.out:
            ap.error("--out required for apply")
        return apply_fixes(a.doc, a.workdir or a.doc + ".work", a.out)
    if a.original:
        fixes = {}
        fx = os.path.join(a.workdir or a.original + ".work", "fixes.json")
        if os.path.isfile(fx):
            with open(fx, encoding="utf-8") as f:
                fixes = json.load(f)
        res = verify(a.original, a.doc, fixes)
        print("VERIFY %s: %s" % (os.path.basename(a.doc), "ok" if res["ok"] else "FAILED"))
        for k, v in res["checks"].items():
            print("  %-16s %s" % (k, "yes" if v else "NO"))
        for p in res["problems"]:
            print("  problem: %s" % p)
        print("  %d issue(s) remain" % res["remaining_hard"])
        return 0 if res["ok"] else 2
    tmp = None if a.workdir else tempfile.mkdtemp(prefix="docx_verify_")
    try:
        return scan(a.doc, a.workdir or tmp)
    finally:
        if tmp:      # every image of the document was extracted here
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
