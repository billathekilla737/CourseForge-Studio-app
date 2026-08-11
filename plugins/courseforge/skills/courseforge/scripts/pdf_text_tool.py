"""pdf_text_tool.py (courseforge) - PDF text SCAN and targeted in-place EDIT.

Complements triage_pdf.py (which classifies) with two things it deliberately
did not do: search visible text + metadata for arbitrary patterns, and replace
short factual strings (a name, an email, a phone, a room number) in place.

  scan  : regex search over every page's extracted text AND the metadata
          dictionary. Reports file, page, term, and surrounding context as
          JSON + console table. Read-only.
  apply : replace literal strings using PyMuPDF redaction annotations - each
          hit's bounding box is white-filled and the replacement text is drawn
          into the same box. Layout is preserved; reflow never happens.
          Optionally rewrites metadata fields (--set-author etc.).
  Both verify: apply re-extracts every page afterward and fails loudly if any
  "find" string survived, and reports which mappings never matched at all.

HONEST SCOPE (do not oversell this):
  - Replacement text is drawn in a base font (Helvetica), not the document's
    embedded font. A close look can tell. Fine for contact lines; wrong tool
    for re-typesetting paragraphs.
  - A replacement much longer than the original is shrunk to fit its box and
    may look cramped; much shorter leaves trailing whitespace. Size mappings
    similarly (name-for-name, email-for-email).
  - Literal matching only (PyMuPDF search_for, case-insensitive). Multi-line
    spans are matched only when MuPDF returns a joined hit; a phrase wrapped
    mid-way may need two shorter mappings.
  - This does NOT create tagged/accessible PDFs. Pair with triage_pdf.py and
    prefer regenerating from the source .docx when one exists.

Usage:
  python pdf_text_tool.py scan  file1.pdf [...] --pattern "smith|jones" [--json out.json]
  python pdf_text_tool.py apply in.pdf --map map.json --out fixed.pdf
         map.json: [ { "find": "Jane Smith", "replace": "John Doe" }, ... ]
                   "replace": "" removes the text (white-out).
  apply options: --set-author "Name"  --set-title "T"  [--json report.json]

Exit codes: 0 ok; 1 usage/IO error; 2 apply verification failed (a find
string is still present in the output).
"""
import argparse
import json
import re
import sys

import pymupdf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_scan(args):
    rx = re.compile(args.pattern, re.I)
    hits = []
    for path in args.pdfs:
        try:
            doc = pymupdf.open(path)
        except Exception as e:
            print("UNREADABLE  %s  (%s)" % (path, e))
            continue
        meta_blob = " ".join("%s=%s" % (k, v) for k, v in (doc.metadata or {}).items() if v)
        for m in rx.finditer(meta_blob):
            hits.append({"file": path, "page": "[metadata]", "term": m.group(0),
                         "context": meta_blob[max(0, m.start() - 60):m.end() + 60]})
        for i, page in enumerate(doc, 1):
            text = page.get_text()
            for m in rx.finditer(text):
                s = max(0, m.start() - 80)
                hits.append({"file": path, "page": i, "term": m.group(0),
                             "context": re.sub(r"\s+", " ", text[s:m.end() + 80])})
        doc.close()
    for h in hits:
        print("HIT  %-40s p%-10s %-18s ...%s..." %
              (h["file"][-40:], h["page"], h["term"], h["context"][:90]))
    print("\n%d hit(s) across %d file(s)" % (len(hits), len(args.pdfs)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(hits, f, indent=1)
    return 0


def cmd_apply(args):
    with open(args.map, encoding="utf-8-sig") as f:
        mappings = json.load(f)
    doc = pymupdf.open(args.pdf)
    replaced = {m["find"]: 0 for m in mappings}
    for page in doc:
        annotated = False
        for m in mappings:
            for rect in page.search_for(m["find"]):
                # white-out the original; draw the replacement into the same box
                page.add_redact_annot(
                    rect, text=m["replace"], fontname="helv",
                    fontsize=max(6.0, rect.height * 0.72), fill=(1, 1, 1))
                replaced[m["find"]] += 1
                annotated = True
        if annotated:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    meta = doc.metadata or {}
    if args.set_author is not None:
        meta["author"] = args.set_author
    if args.set_title is not None:
        meta["title"] = args.set_title
    doc.set_metadata(meta)
    doc.save(args.out, garbage=3, deflate=True)
    doc.close()

    # verify: no "find" string may survive anywhere in the output
    check = pymupdf.open(args.out)
    all_text = " ".join(p.get_text() for p in check)
    all_text += " " + " ".join("%s" % v for v in (check.metadata or {}).values() if v)
    check.close()
    residual = {m["find"]: len(re.findall(re.escape(m["find"]), all_text, re.I))
                for m in mappings}
    report = {"input": args.pdf, "output": args.out,
              "replacements": replaced, "residual": residual,
              "unmatched_mappings": [k for k, v in replaced.items() if v == 0]}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    for k, v in replaced.items():
        flag = "" if residual[k] == 0 else "   RESIDUAL=%d !!" % residual[k]
        print("%-52s x%d%s" % (k[:52], v, flag))
    if report["unmatched_mappings"]:
        print("never matched (check wording/hyphenation): %s"
              % ", ".join(report["unmatched_mappings"]))
    bad = sum(residual.values())
    print("\n%s  (%d replacement(s), %d residual)"
          % ("CLEAN" if bad == 0 else "VERIFY FAILED", sum(replaced.values()), bad))
    return 0 if bad == 0 else 2


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("pdfs", nargs="+")
    s.add_argument("--pattern", required=True)
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scan)
    a = sub.add_parser("apply")
    a.add_argument("pdf")
    a.add_argument("--map", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--set-author", default=None)
    a.add_argument("--set-title", default=None)
    a.add_argument("--json")
    a.set_defaults(fn=cmd_apply)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
