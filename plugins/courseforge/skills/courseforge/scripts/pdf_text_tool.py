"""pdf_text_tool.py (courseforge) - PDF text SCAN, targeted EDIT, and form FILL.

Complements triage_pdf.py (which only classifies) with the three things it did
not do: search text + metadata for patterns, replace short factual strings in
place, and write values onto forms that have no fillable fields.

SUBCOMMANDS
  scan   regex search over every page's text AND the metadata dict. Also flags
         structural hazards a caller must know about before editing:
         digital signatures, low/no text layer, an outline (TOC), and repeated
         matches. Read-only.
  apply  replace literal strings via redaction annotations - each hit's box is
         white-filled and the replacement drawn into the same box, so layout
         never reflows. Optional metadata rewrite and TOC rewrite.
  fill   INSERT text that is not replacing anything: anchored just right of a
         label ("Full Name: ____") or at absolute coordinates. This is how you
         complete a flattened or blank form that has no AcroForm widgets.

  apply and fill both verify their own work and exit non-zero on failure.

SIGNED DOCUMENTS
  Any write to a signed PDF invalidates the cryptographic signature. apply and
  fill REFUSE on a signed document unless --allow-signed is passed, and say so.
  Detection: get_sigflags() >= 1, or a signature widget, or /ByteRange present.

HONEST SCOPE (do not oversell this)
  - Inserted/replacement text is drawn in a base font (Helvetica), not the
    document's embedded font. Close inspection can tell. Right tool for names,
    dates, emails, phones, room numbers; wrong tool for re-typesetting prose.
  - A replacement longer than the original is shrunk to fit its box; a much
    shorter one leaves a whitespace gap. Size mappings like-for-like.
  - Matching uses the SAME text extraction you see in `scan`. In scanned or
    ligature-mangled documents the extracted string differs from what a human
    reads (checkbox glyphs come out as "D", "0", "o"), so a phrase you can see
    may be unfindable. Always scan first and copy the exact string from output.
  - Line-wrapped phrases are two separate strings. Map each rendered line.
  - Replace-all is the default per mapping; set "limit": N for the first N hits.
  - This does NOT create tagged/accessible PDFs. Pair with triage_pdf.py, and
    prefer regenerating from a source .docx when one exists.

USAGE
  python pdf_text_tool.py scan f1.pdf [f2 ...] --pattern "regex" [--json out.json]
  python pdf_text_tool.py apply in.pdf --map map.json --out fixed.pdf
        map.json: [ {"find":"Jane Smith","replace":"John Doe","limit":1}, ... ]
                  "replace":"" removes the text (white-out).
        options: --set-author X  --set-title X  --update-toc  --allow-signed
  python pdf_text_tool.py fill in.pdf --map fill.json --out filled.pdf
        options: --preview p.png (numbered boxes; red=collision)  --dry-run
        fill.json: [ {"page":1,"anchor":"Full Name:","dx":8,"text":"John A. Doe"},
                     {"page":1,"at":[220,415],"text":"555-0142","size":10} ]
        per-item: dx/dy offsets (pt), size, occurrence (1-based, default 1),
                  allow_overlap:true to permit deliberate overprinting
  fill REFUSES to write if any value would land on existing non-rule text,
  overlap another placement, or run off the page. Underscore/dot leader runs are
  treated as the blank being filled, not as a collision.

EXIT CODES  0 ok | 1 usage/IO | 2 verification failed | 3 refused (signed)
"""
import argparse
import json
import re
import sys

import pymupdf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# A full page carrying under ~200 chars is a form/scan drawn as vectors or an
# image: `apply` has almost nothing to match there, so `fill` is the only route.
# Deliberately generous - this is a NOTE, and a false positive costs nothing.
LOW_TEXT_CHARS = 200


def signature_state(doc):
    """Return (is_signed, reason) - conservative: any hint counts."""
    reasons = []
    try:
        if doc.get_sigflags() >= 1:
            reasons.append("get_sigflags=%d" % doc.get_sigflags())
    except Exception:
        pass
    for page in doc:
        for w in (page.widgets() or []):
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                reasons.append("signature widget %r" % (w.field_name or "?"))
    try:
        if b"/ByteRange" in doc.tobytes():
            reasons.append("/ByteRange present")
    except Exception:
        pass
    return (bool(reasons), "; ".join(reasons))


def guard_signed(doc, allow, strip=False):
    """Return True if writing may proceed. Optionally remove signature fields.

    Editing a signed PDF leaves the PKCS7 blob and /ByteRange copied verbatim
    while the surrounding bytes shift, so the digest can no longer match. The
    file then reads as "signed but ALTERED" in a viewer, which is worse than
    unsigned - it looks like tampering. --strip-signature removes the signature
    widgets so the output is honestly unsigned instead.
    """
    signed, why = signature_state(doc)
    if signed:
        print("SIGNED DOCUMENT DETECTED: %s" % why)
        if strip:
            removed = 0
            for page in doc:
                for w in list(page.widgets() or []):
                    if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                        try:
                            page.delete_widget(w)
                            removed += 1
                        except Exception as e:
                            print("  could not remove widget %r: %s" % (w.field_name, e))
            print("  --strip-signature: removed %d signature widget(s); output will be "
                  "unsigned rather than invalid-signed." % removed)
            return True
        if not allow:
            print("REFUSED: writing would invalidate the signature. Use "
                  "--strip-signature to drop it honestly, or --allow-signed to "
                  "keep a signature that will read as ALTERED.")
            return False
        print("WARNING: --allow-signed given. The signature blob is preserved but the "
              "content changes, so viewers will report the document as ALTERED. "
              "Prefer --strip-signature unless you specifically want that.")
    return True


def report_hazards(doc, path, mappings=None):
    """Print structural warnings a caller needs before/after editing."""
    signed, why = signature_state(doc)
    if signed:
        print("  HAZARD signed        : %s" % why)
    low = [i + 1 for i, p in enumerate(doc) if len(p.get_text().strip()) < LOW_TEXT_CHARS]
    if low:
        print("  HAZARD low/no text   : page(s) %s - image-only or vector-drawn; "
              "text replacement cannot reach content there (use `fill`)."
              % ", ".join(map(str, low)))
    toc = doc.get_toc()
    if toc:
        print("  NOTE   outline/TOC   : %d entr(ies); replacing heading text does NOT "
              "update these (use --update-toc)." % len(toc))
        if mappings:
            stale = [t[1] for t in toc
                     if any(m["find"].lower() in t[1].lower() for m in mappings)]
            if stale:
                print("  HAZARD stale TOC     : %s" % "; ".join(repr(s) for s in stale))


def cmd_scan(args):
    rx = re.compile(args.pattern, re.I)
    hits = []
    for path in args.pdfs:
        try:
            doc = pymupdf.open(path)
        except Exception as e:
            print("UNREADABLE  %s  (%s)" % (path, e))
            continue
        print("--- %s (%d page(s)) ---" % (path, len(doc)))
        report_hazards(doc, path)
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
        print("HIT  %-34s p%-10s %-20s ...%s..." %
              (h["file"][-34:], h["page"], h["term"][:20], h["context"][:80]))
    print("\n%d hit(s) across %d file(s)" % (len(hits), len(args.pdfs)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(hits, f, indent=1)
    return 0


def cmd_apply(args):
    with open(args.map, encoding="utf-8-sig") as f:
        mappings = json.load(f)
    doc = pymupdf.open(args.pdf)
    if not guard_signed(doc, args.allow_signed, args.strip_signature):
        return 3
    report_hazards(doc, args.pdf, mappings)

    replaced = {m["find"]: 0 for m in mappings}
    per_page = {}
    overlaps = []
    for pno, page in enumerate(doc, 1):
        # Two passes. All redaction annots are applied together, so two mappings
        # whose matches OVERLAP (e.g. "39532" nested inside "123 Main St, MS 39532")
        # would each draw into the same pixels and the page ends up with doubled
        # text. Collect candidate rects first, keep the earliest-listed mapping for
        # any overlapping region, and report what was skipped. This is why the map
        # should list the most specific string first.
        planned = []
        for m in mappings:
            limit = m.get("limit")
            for rect in page.search_for(m["find"]):
                if limit is not None and replaced[m["find"]] >= limit:
                    break
                clash = next((p for p in planned if (p[0] & rect).get_area() > 0), None)
                if clash:
                    overlaps.append({"page": pno, "skipped": m["find"],
                                     "overlapped_by": clash[1]["find"]})
                    continue
                planned.append((rect, m))
                replaced[m["find"]] += 1
        for rect, m in planned:
            page.add_redact_annot(
                rect, text=m["replace"], fontname="helv",
                fontsize=max(6.0, rect.height * 0.72), fill=(1, 1, 1))
        if planned:
            per_page[pno] = len(planned)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    if overlaps:
        print("  NOTE   overlaps      : %d nested match(es) skipped so text is not "
              "double-drawn:" % len(overlaps))
        for o in overlaps[:8]:
            print("           p%d %r inside %r" % (o["page"], o["skipped"], o["overlapped_by"]))

    if args.update_toc:
        toc = doc.get_toc()
        if toc:
            new_toc, changed = [], 0
            for lvl, title, pg in toc:
                nt = title
                for m in mappings:
                    nt = re.sub(re.escape(m["find"]), m["replace"], nt, flags=re.I)
                if nt != title:
                    changed += 1
                new_toc.append([lvl, nt, pg])
            doc.set_toc(new_toc)
            print("  TOC rewritten: %d of %d entr(ies) changed" % (changed, len(toc)))

    meta = doc.metadata or {}
    if args.set_author is not None:
        meta["author"] = args.set_author
    if args.set_title is not None:
        meta["title"] = args.set_title
    doc.set_metadata(meta)
    doc.save(args.out, garbage=3, deflate=True)
    doc.close()

    check = pymupdf.open(args.out)
    blob = " ".join(p.get_text() for p in check)
    blob += " " + " ".join("%s" % v for v in (check.metadata or {}).values() if v)
    blob += " " + " ".join(t[1] for t in check.get_toc())
    check.close()
    residual = {}
    for m in mappings:
        n = len(re.findall(re.escape(m["find"]), blob, re.I))
        # a mapping with an explicit limit is expected to leave the rest behind
        residual[m["find"]] = 0 if m.get("limit") is not None else n
    report = {"input": args.pdf, "output": args.out, "replacements": replaced,
              "residual": residual, "per_page": per_page,
              "unmatched_mappings": [k for k, v in replaced.items() if v == 0]}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    for k, v in replaced.items():
        flag = "" if residual[k] == 0 else "   RESIDUAL=%d !!" % residual[k]
        print("%-52s x%d%s" % (k[:52], v, flag))
    if report["unmatched_mappings"]:
        print("NEVER MATCHED (check exact wording via `scan`, or it wraps lines): %s"
              % ", ".join(repr(u) for u in report["unmatched_mappings"]))
    bad = sum(residual.values())
    print("\n%s  (%d replacement(s), %d residual)"
          % ("CLEAN" if bad == 0 else "VERIFY FAILED", sum(replaced.values()), bad))
    return 0 if bad == 0 else 2


BLANK_CHARS = set("_.Â·â€¤â€¥â€¦-â€“â€” \t")


def _is_blank_filler(word):
    """True for a 'word' that is only rule/leader characters (____ or ....).

    Those ARE the blank a value is meant to be written onto, so overlapping them
    is correct behaviour, not a collision.
    """
    return bool(word) and all(ch in BLANK_CHARS for ch in word)


def _glyph_rect(x, y, text, size):
    """Approximate ink box for text drawn with baseline at (x, y)."""
    w = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    return pymupdf.Rect(x, y - size * 0.80, x + w, y + size * 0.22)


def cmd_fill(args):
    with open(args.map, encoding="utf-8-sig") as f:
        items = json.load(f)
    doc = pymupdf.open(args.pdf)
    if not guard_signed(doc, args.allow_signed, args.strip_signature):
        return 3

    # ---- resolve every placement BEFORE drawing anything -------------------
    # Anchored placement is "find the label, step right by dx". That is fragile:
    # on a multi-column form, or where a label repeats in another table cell, the
    # value lands on top of unrelated content - and a plain text-presence check
    # cannot see it, because the text IS in the file, just in the wrong place.
    # So resolve, then geometrically check each target against (a) existing words
    # that are not blank rules, (b) other planned placements, (c) the page edge.
    plans, missing = [], []
    for idx, it in enumerate(items, 1):
        pno = int(it.get("page", 1)) - 1
        if pno < 0 or pno >= len(doc):
            missing.append("#%d %r: page %s out of range" % (idx, it.get("text"), it.get("page")))
            continue
        page = doc[pno]
        size = float(it.get("size", 10))
        anchor_rect = None
        if "at" in it:
            x, y = float(it["at"][0]), float(it["at"][1])
        else:
            anchor = it.get("anchor")
            if not anchor:
                missing.append("#%d %r: needs 'anchor' or 'at'" % (idx, it.get("text")))
                continue
            rects = page.search_for(anchor)
            occ = int(it.get("occurrence", 1))
            if len(rects) < occ:
                missing.append("#%d anchor %r occurrence %d not found on page %d (found %d)"
                               % (idx, anchor, occ, pno + 1, len(rects)))
                continue
            anchor_rect = rects[occ - 1]
            x = anchor_rect.x1 + float(it.get("dx", 6))
            # Lift the baseline slightly. A label's rect bottom sits level with the
            # underscore rule that follows it, so inserting at y1 exactly makes the
            # rule strike through the value. -2.5pt puts the text ON the line.
            y = anchor_rect.y1 + float(it.get("dy", -2.5))
        plans.append({"idx": idx, "item": it, "page": pno, "x": x, "y": y,
                      "size": size, "rect": _glyph_rect(x, y, it["text"], size),
                      "anchor_rect": anchor_rect, "problems": []})

    # ---- geometric checks --------------------------------------------------
    words_cache = {}
    for p in plans:
        page = doc[p["page"]]
        if p["page"] not in words_cache:
            words_cache[p["page"]] = page.get_text("words")
        tgt = p["rect"]
        p["notes"] = []
        if tgt.x1 > page.rect.x1 - 4 or tgt.y0 < page.rect.y0 or tgt.y1 > page.rect.y1:
            p["problems"].append("runs past the page edge (x1=%.0f, page width %.0f)"
                                 % (tgt.x1, page.rect.x1))
        # Wrong-line hazard: a value that is not on its own label's baseline is
        # usually about to fill the WRONG blank (blanks are rule characters, so
        # the collision check cannot see the difference). Warn, don't refuse -
        # an explicit dy is sometimes intentional (value goes in a box above).
        if p["anchor_rect"] is not None:
            drift = abs(((tgt.y0 + tgt.y1) / 2)
                        - ((p["anchor_rect"].y0 + p["anchor_rect"].y1) / 2))
            if drift > max(6.0, p["size"] * 0.9):
                p["notes"].append("sits %.0fpt off its anchor's baseline - check this "
                                  "is the intended blank (use --preview)" % drift)
        for w in words_cache[p["page"]]:
            wr = pymupdf.Rect(w[:4])
            word = w[4]
            if _is_blank_filler(word):
                continue
            if p["anchor_rect"] is not None and wr.intersects(p["anchor_rect"]):
                continue          # the label we anchored to
            inter = wr & tgt
            if inter.get_area() > 0.35 * min(wr.get_area() or 1, tgt.get_area() or 1):
                p["problems"].append("overlaps existing text %r at (%.0f,%.0f)"
                                     % (word[:24], wr.x0, wr.y0))
        for q in plans:
            if q is p or q["page"] != p["page"]:
                continue
            if q["idx"] < p["idx"] and (q["rect"] & tgt).get_area() > 0:
                p["problems"].append("overlaps placement #%d (%r)"
                                     % (q["idx"], q["item"]["text"][:24]))

    collisions = [p for p in plans
                  if p["problems"] and not p["item"].get("allow_overlap")]

    # ---- optional visual preview ------------------------------------------
    if args.preview:
        import os
        base, ext = os.path.splitext(args.preview)
        ext = ext or ".png"
        for pno in sorted({p["page"] for p in plans}):
            page = doc[pno]
            shape = page.new_shape()
            for p in [q for q in plans if q["page"] == pno]:
                bad = bool(p["problems"]) and not p["item"].get("allow_overlap")
                col = (0.85, 0, 0) if bad else (0, 0.55, 0)
                shape.draw_rect(p["rect"])
                shape.finish(color=col, width=0.7)
                shape.insert_text((p["rect"].x0, p["rect"].y0 - 1.5), "#%d" % p["idx"],
                                  fontname="helv", fontsize=5.5, color=col)
            shape.commit(overlay=True)
            out = "%s-p%d%s" % (base, pno + 1, ext)
            page.get_pixmap(dpi=150).save(out)
            print("  preview -> %s" % out)
        doc.close()
        # preview must never be mistaken for a committed edit
        doc = pymupdf.open(args.pdf)
        if not guard_signed(doc, args.allow_signed, args.strip_signature):
            return 3

    for p in plans:
        tag = "OK  " if not p["problems"] else ("WARN" if p["item"].get("allow_overlap") else "BAD ")
        print("  %s #%-2d %-28r p%d at (%.0f, %.0f) size %.1f w=%.0f"
              % (tag, p["idx"], p["item"]["text"][:28], p["page"] + 1,
                 p["x"], p["y"], p["size"], p["rect"].width))
        for prob in p["problems"]:
            print("            %s %s" % ("(allowed)" if p["item"].get("allow_overlap") else "->", prob))
        for note in p.get("notes", []):
            print("            NOTE %s" % note)

    if missing:
        print("\nUNPLACED:")
        for m in missing:
            print("  %s" % m)
    if collisions:
        print("\nPLACEMENT COLLISIONS (%d) - refusing to write. Adjust dx/dy/size/"
              "occurrence, or set \"allow_overlap\": true on an item that is meant to "
              "overprint." % len(collisions))

    if missing or collisions:
        print("\nVERIFY FAILED  (0 written, %d unplaced, %d colliding)"
              % (len(missing), len(collisions)))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump({"written": 0, "unplaced": missing,
                           "collisions": [{"idx": p["idx"], "text": p["item"]["text"],
                                           "problems": p["problems"]} for p in collisions]},
                          f, indent=1)
        doc.close()
        return 2

    if args.dry_run:
        print("\nDRY RUN  (%d placement(s) validated, nothing written)" % len(plans))
        doc.close()
        return 0

    for p in plans:
        doc[p["page"]].insert_text((p["x"], p["y"]), p["item"]["text"],
                                   fontname="helv", fontsize=p["size"], color=(0, 0, 0))
    placed = len(plans)
    doc.save(args.out, garbage=3, deflate=True)
    doc.close()

    check = pymupdf.open(args.out)
    blob = " ".join(p.get_text() for p in check)
    check.close()
    absent = [it["text"] for it in items if it["text"] and it["text"] not in blob]
    if absent:
        print("\nNOT FOUND IN OUTPUT TEXT (drawn but unextractable): %s"
              % ", ".join(repr(a) for a in absent))
    ok = not absent
    print("\n%s  (%d placed, 0 unplaced, %d unverified)"
          % ("CLEAN" if ok else "VERIFY FAILED", placed, len(absent)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"placed": placed, "unplaced": [], "absent": absent,
                       "warned_overlaps": [{"idx": p["idx"], "text": p["item"]["text"],
                                            "problems": p["problems"]}
                                           for p in plans if p["problems"]]},
                      f, indent=1)
    return 0 if ok else 2


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
    a.add_argument("--update-toc", action="store_true")
    a.add_argument("--allow-signed", action="store_true")
    a.add_argument("--strip-signature", action="store_true")
    a.add_argument("--json")
    a.set_defaults(fn=cmd_apply)

    f = sub.add_parser("fill")
    f.add_argument("pdf")
    f.add_argument("--map", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--allow-signed", action="store_true")
    f.add_argument("--strip-signature", action="store_true")
    f.add_argument("--preview", default=None,
                   help="write PNG(s) with numbered boxes showing each placement")
    f.add_argument("--dry-run", action="store_true",
                   help="validate placements only; write no PDF")
    f.add_argument("--json")
    f.set_defaults(fn=cmd_fill)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
