"""
triage_pdf.py (courseforge) - PDF accessibility TRIAGE. Detection only.

Classifies each PDF so a designer knows which of a course's PDFs are hurting
the Ally score and what kind of work each needs. It deliberately does NOT
claim to remediate: real PDF/UA tagging and reading order are a manual
(Acrobat) job. The win here is knowing, across 40 PDFs, which 6 are scanned
images with no text layer (the worst Ally offenders) versus which are merely
untagged text.

Classes (worst first):
  scanned-image : most pages have images and NO extractable text -> needs OCR
                  at minimum, ideally re-sourcing the original document
  text-untagged : has a text layer but no PDF tag structure (/MarkInfo)
                  -> screen readers get words but no headings/reading order
  tagged        : claims a tag structure (/Marked true) - verify manually,
                  tags can exist and still be junk
  encrypted     : cannot inspect (password/permissions)
  empty/odd     : no text and no images

Usage:
  python triage_pdf.py file1.pdf [file2.pdf ...] [--json out.json]
"""
import argparse, json, os, sys

from pypdf import PdfReader

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEVERITY = {"scanned-image": 3, "encrypted": 2, "text-untagged": 2,
            "empty/odd": 1, "tagged": 0}


def triage_one(path):
    r = {"file": os.path.basename(path), "path": path}
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                r.update(cls="encrypted", pages=None,
                         note="password-protected; cannot inspect")
                return r
        n = len(reader.pages)
        text_pages = img_pages = 0
        for pg in reader.pages:
            try:
                has_text = len((pg.extract_text() or "").strip()) > 20
            except Exception:
                has_text = False
            try:
                has_img = len(pg.images) > 0
            except Exception:
                has_img = False
            if has_text:
                text_pages += 1
            if has_img:
                img_pages += 1
        marked = False
        try:
            mi = reader.trailer["/Root"].get("/MarkInfo")
            marked = bool(mi and mi.get("/Marked"))
        except Exception:
            pass
        r.update(pages=n, text_pages=text_pages, image_pages=img_pages, tagged=marked)
        if n and text_pages / n < 0.5 and img_pages > 0:
            r["cls"] = "scanned-image"
            r["note"] = "%d/%d pages have no text layer -> OCR needed at minimum" % (n - text_pages, n)
        elif text_pages == 0 and img_pages == 0:
            r["cls"] = "empty/odd"
            r["note"] = "no text and no images extracted"
        elif marked:
            r["cls"] = "tagged"
            r["note"] = "has a tag structure - spot-check quality manually"
        else:
            r["cls"] = "text-untagged"
            r["note"] = "text layer present, no tags - headings/reading order absent for screen readers"
    except Exception as e:
        r.update(cls="empty/odd", pages=None, note="unreadable: %s" % e)
    r["severity"] = SEVERITY.get(r.get("cls"), 1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    results = sorted((triage_one(p) for p in a.pdfs),
                     key=lambda r: -r["severity"])
    print("%-38s %-14s %-8s %s" % ("FILE", "CLASS", "PAGES", "NOTE"))
    print("-" * 100)
    for r in results:
        print("%-38s %-14s %-8s %s" % (r["file"][:38], r.get("cls"),
                                       r.get("pages", "?"), r.get("note", "")))
    worst = [r for r in results if r["severity"] >= 3]
    print("-" * 100)
    print("%d file(s); %d scanned-image (fix these first)" % (len(results), len(worst)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
        print("json: %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
