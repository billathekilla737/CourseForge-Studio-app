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
  python -m courseforge.docs.pdf_text scan f1.pdf [f2 ...] --pattern "regex" [--json out.json]
  python -m courseforge.docs.pdf_text apply in.pdf --map map.json --out fixed.pdf
        map.json: [ {"find":"Jane Smith","replace":"John Doe","limit":1}, ... ]
                  "replace":"" removes the text (white-out).
        options: --set-author X  --set-title X  --update-toc  --allow-signed
  python -m courseforge.docs.pdf_text fill in.pdf --map fill.json --out filled.pdf
        options: --preview p.png (numbered boxes; red=collision)  --dry-run
        fill.json: [ {"page":1,"anchor":"Full Name:","dx":8,"text":"John A. Doe"},
                     {"page":1,"at":[220,415],"text":"555-0142","size":10} ]
        per-item: dx/dy offsets (pt), size, occurrence (1-based, default 1),
                  allow_overlap:true to permit deliberate overprinting
  fill REFUSES to write if any value would land on existing non-rule text,
  overlap another placement, or run off the page. Underscore/dot leader runs are
  treated as the blank being filled, not as a collision.

Library surface (what the Studio's gateway calls; nothing here prints):
  scan_file(path, pattern) -> dict
  apply_map(path, mappings, out, set_author=None, set_title=None, update_toc=False,
            allow_signed=False, strip_signature=False) -> dict
  fill_map(path, items, out, dry_run=False, allow_signed=False, strip_signature=False,
           preview=None) -> dict
  verify_file(original, out, mappings) -> dict

EXIT CODES  0 ok | 1 usage/IO | 2 verification failed | 3 refused (signed)
"""
import argparse
import json
import os
import re
import sys

import pymupdf

# A full page carrying under ~200 chars is a form/scan drawn as vectors or an
# image: `apply` has almost nothing to match there, so `fill` is the only route.
# Deliberately generous - this is a NOTE, and a false positive costs nothing.
LOW_TEXT_CHARS = 200


class Refused(Exception):
    """Writing was refused (signed document) and nothing was written."""


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


def guard_signed(doc, allow, strip=False, notes=None):
    """Return True if writing may proceed. Optionally remove signature fields.

    Editing a signed PDF leaves the PKCS7 blob and /ByteRange copied verbatim
    while the surrounding bytes shift, so the digest can no longer match. The
    file then reads as "signed but ALTERED" in a viewer, which is worse than
    unsigned - it looks like tampering. strip removes the signature widgets so
    the output is honestly unsigned instead.
    """
    notes = notes if notes is not None else []
    signed, why = signature_state(doc)
    if signed:
        notes.append("signed document detected: %s" % why)
        if strip:
            removed = 0
            for page in doc:
                for w in list(page.widgets() or []):
                    if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                        try:
                            page.delete_widget(w)
                            removed += 1
                        except Exception as e:
                            notes.append("could not remove widget %r: %s" % (w.field_name, e))
            notes.append("strip signature: removed %d signature widget(s); output will be "
                         "unsigned rather than invalid-signed." % removed)
            return True
        if not allow:
            notes.append("refused: writing would invalidate the signature. Strip the "
                         "signature to drop it honestly, or allow signed to keep a "
                         "signature that will read as ALTERED.")
            return False
        notes.append("allow signed: the signature blob is preserved but the content "
                     "changes, so viewers will report the document as ALTERED.")
    return True


def hazards(doc, mappings=None):
    """Structural facts a caller needs before/after editing, as a dict."""
    signed, why = signature_state(doc)
    low = [i + 1 for i, p in enumerate(doc) if len(p.get_text().strip()) < LOW_TEXT_CHARS]
    toc = doc.get_toc()
    out = {"signed": signed, "signed_reason": why, "low_text_pages": low,
           "toc_entries": len(toc), "stale_toc": []}
    if toc and mappings:
        out["stale_toc"] = [t[1] for t in toc
                            if any(m["find"].lower() in t[1].lower() for m in mappings)]
    return out


def hazard_lines(h):
    lines = []
    if h["signed"]:
        lines.append("  HAZARD signed        : %s" % h["signed_reason"])
    if h["low_text_pages"]:
        lines.append("  HAZARD low/no text   : page(s) %s - image-only or vector-drawn; "
                     "text replacement cannot reach content there (use `fill`)."
                     % ", ".join(map(str, h["low_text_pages"])))
    if h["toc_entries"]:
        lines.append("  NOTE   outline/TOC   : %d entr(ies); replacing heading text does NOT "
                     "update these (use --update-toc)." % h["toc_entries"])
        if h["stale_toc"]:
            lines.append("  HAZARD stale TOC     : %s" % "; ".join(repr(s) for s in h["stale_toc"]))
    return lines


def _normalize(mappings):
    if isinstance(mappings, dict):
        mappings = [{"find": k, "replace": v} for k, v in mappings.items()]
    out = []
    for m in mappings or []:
        find = m.get("find") or ""
        if not find:
            continue
        entry = {"find": find, "replace": m.get("replace") or ""}
        if m.get("limit") is not None:
            try:
                entry["limit"] = int(m["limit"])
            except (TypeError, ValueError):
                pass
        out.append(entry)
    return out


# ---------- scan ----------

def scan_file(path, pattern):
    """Regex search over every page's text and the metadata. Read-only."""
    res = {"file": str(path), "hits": [], "pages": 0, "pattern": pattern}
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        res.update(unreadable=True, reason=str(e), hazards={})
        return res
    with doc:
        res["pages"] = len(doc)
        res["hazards"] = hazards(doc)
        rx = re.compile(pattern, re.I)
        meta_blob = " ".join("%s=%s" % (k, v) for k, v in (doc.metadata or {}).items() if v)
        for m in rx.finditer(meta_blob):
            res["hits"].append({"page": "[metadata]", "term": m.group(0),
                                "context": meta_blob[max(0, m.start() - 60):m.end() + 60]})
        for i, page in enumerate(doc, 1):
            text = page.get_text()
            for m in rx.finditer(text):
                s = max(0, m.start() - 80)
                res["hits"].append({"page": i, "term": m.group(0),
                                    "context": re.sub(r"\s+", " ", text[s:m.end() + 80])})
    res["unreadable"] = False
    return res


def cmd_scan(args):
    hits = []
    for path in args.pdfs:
        res = scan_file(path, args.pattern)
        if res.get("unreadable"):
            print("UNREADABLE  %s  (%s)" % (path, res["reason"]))
            continue
        print("--- %s (%d page(s)) ---" % (path, res["pages"]))
        for line in hazard_lines(res["hazards"]):
            print(line)
        for h in res["hits"]:
            hits.append({"file": path, **h})
    for h in hits:
        print("HIT  %-34s p%-10s %-20s ...%s..." %
              (h["file"][-34:], h["page"], h["term"][:20], h["context"][:80]))
    print("\n%d hit(s) across %d file(s)" % (len(hits), len(args.pdfs)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(hits, f, indent=1)
    return 0


# ---------- apply ----------

def verify_file(original, out, mappings):
    """Zero residual (limits respected), page count unchanged, file opens."""
    mappings = _normalize(mappings)
    problems = []
    residual = {m["find"]: 0 for m in mappings}
    try:
        check = pymupdf.open(out)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "opens": False, "residual": residual,
                "problems": ["the fixed file does not open: %s" % exc]}
    with check:
        blob = " ".join(p.get_text() for p in check)
        blob += " " + " ".join("%s" % v for v in (check.metadata or {}).values() if v)
        blob += " " + " ".join(t[1] for t in check.get_toc())
        pages_out = len(check)
    pages_in = None
    if original:
        try:
            with pymupdf.open(original) as o:
                pages_in = len(o)
        except Exception:  # noqa: BLE001
            pages_in = None
    for m in mappings:
        n = len(re.findall(re.escape(m["find"]), blob, re.I))
        # a mapping with an explicit limit is expected to leave the rest behind
        residual[m["find"]] = 0 if m.get("limit") is not None else n
    left = {k: v for k, v in residual.items() if v}
    if left:
        problems.append("text still present: " + ", ".join("%r x%d" % kv for kv in list(left.items())[:6]))
    pages_same = pages_in is None or pages_in == pages_out
    if not pages_same:
        problems.append("page count changed (%s to %s)" % (pages_in, pages_out))
    return {"ok": not problems, "opens": True, "pages_same": pages_same,
            "residual": residual, "problems": problems}


def apply_map(path, mappings, out, set_author=None, set_title=None, update_toc=False,
              allow_signed=False, strip_signature=False):
    """Replace every mapping in place via redactions, write `out`, verify it."""
    mappings = _normalize(mappings)
    notes = []
    doc = pymupdf.open(path)
    if not guard_signed(doc, allow_signed, strip_signature, notes):
        haz = hazards(doc, mappings)
        doc.close()
        return {"ok": False, "refused": True, "reason": "signed document", "notes": notes,
                "hazards": haz, "replacements": {}, "residual": {}, "per_page": {},
                "unmatched_mappings": [], "overlaps": [], "total": 0}
    haz = hazards(doc, mappings)

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
            per_page[str(pno)] = len(planned)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

    toc_changed = 0
    if update_toc:
        toc = doc.get_toc()
        if toc:
            new_toc = []
            for lvl, title, pg in toc:
                nt = title
                for m in mappings:
                    nt = re.sub(re.escape(m["find"]), m["replace"], nt, flags=re.I)
                if nt != title:
                    toc_changed += 1
                new_toc.append([lvl, nt, pg])
            doc.set_toc(new_toc)
            notes.append("TOC rewritten: %d of %d entr(ies) changed" % (toc_changed, len(toc)))

    meta = doc.metadata or {}
    if set_author is not None:
        meta["author"] = set_author
    if set_title is not None:
        meta["title"] = set_title
    doc.set_metadata(meta)
    doc.save(out, garbage=3, deflate=True)
    doc.close()

    ver = verify_file(path, out, mappings)
    return {"ok": ver["ok"], "refused": False, "input": str(path), "output": str(out),
            "replacements": replaced, "residual": ver["residual"], "per_page": per_page,
            "unmatched_mappings": [k for k, v in replaced.items() if v == 0],
            "overlaps": overlaps, "hazards": haz, "notes": notes, "problems": ver["problems"],
            "toc_changed": toc_changed, "total": sum(replaced.values())}


def cmd_apply(args):
    with open(args.map, encoding="utf-8-sig") as f:
        mappings = json.load(f)
    res = apply_map(args.pdf, mappings, args.out, set_author=args.set_author,
                    set_title=args.set_title, update_toc=args.update_toc,
                    allow_signed=args.allow_signed, strip_signature=args.strip_signature)
    for n in res["notes"]:
        print("  %s" % n)
    if res.get("refused"):
        print("REFUSED: %s" % res["reason"])
        return 3
    for line in hazard_lines(res["hazards"]):
        print(line)
    if res["overlaps"]:
        print("  NOTE   overlaps      : %d nested match(es) skipped so text is not "
              "double-drawn:" % len(res["overlaps"]))
        for o in res["overlaps"][:8]:
            print("           p%d %r inside %r" % (o["page"], o["skipped"], o["overlapped_by"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    for k, v in res["replacements"].items():
        left = res["residual"].get(k, 0)
        flag = "" if left == 0 else "   RESIDUAL=%d !!" % left
        print("%-52s x%d%s" % (k[:52], v, flag))
    if res["unmatched_mappings"]:
        print("NEVER MATCHED (check exact wording via `scan`, or it wraps lines): %s"
              % ", ".join(repr(u) for u in res["unmatched_mappings"]))
    bad = sum(res["residual"].values())
    print("\n%s  (%d replacement(s), %d residual)"
          % ("CLEAN" if res["ok"] else "VERIFY FAILED", res["total"], bad))
    return 0 if res["ok"] else 2


# ---------- fill ----------

# Rule/leader characters, as explicit escapes. This line used to be
# MOJIBAKE: the intended dot leaders and dashes had been UTF-8 encoded
# twice, so the set actually held stray characters (A-circumflex, euro
# sign, curly quotes) while MISSING the real ellipsis and en/em dashes it
# was supposed to match. Escapes cannot be corrupted by a round-trip
# through the wrong encoding.
BLANK_CHARS = set(
    "_."                     # underscore rules, period leaders
    "·"                 # MIDDLE DOT
    "․‥…"       # ONE/TWO DOT LEADER, HORIZONTAL ELLIPSIS
    "-–—"            # hyphen, EN DASH, EM DASH
    " \t"
)


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


def _plan_fill(doc, items):
    """Resolve every placement BEFORE drawing anything.

    Anchored placement is "find the label, step right by dx". That is fragile:
    on a multi-column form, or where a label repeats in another table cell, the
    value lands on top of unrelated content - and a plain text-presence check
    cannot see it, because the text IS in the file, just in the wrong place.
    So resolve, then geometrically check each target against (a) existing words
    that are not blank rules, (b) other planned placements, (c) the page edge.
    """
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
                      "anchor_rect": anchor_rect, "problems": [], "notes": []})

    words_cache = {}
    for p in plans:
        page = doc[p["page"]]
        if p["page"] not in words_cache:
            words_cache[p["page"]] = page.get_text("words")
        tgt = p["rect"]
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
    return plans, missing


def _plan_rows(plans):
    return [{"idx": p["idx"], "text": p["item"]["text"], "page": p["page"] + 1,
             "x": round(p["x"], 1), "y": round(p["y"], 1), "size": p["size"],
             "width": round(p["rect"].width, 1), "problems": p["problems"],
             "notes": p["notes"], "allow_overlap": bool(p["item"].get("allow_overlap"))}
            for p in plans]


def fill_map(path, items, out, dry_run=False, allow_signed=False, strip_signature=False,
             preview=None):
    """Insert values onto a form. Refuses on collisions; verifies its own output."""
    notes = []
    doc = pymupdf.open(path)
    if not guard_signed(doc, allow_signed, strip_signature, notes):
        doc.close()
        return {"ok": False, "refused": True, "reason": "signed document", "notes": notes,
                "placed": 0, "unplaced": [], "collisions": [], "plans": []}
    plans, missing = _plan_fill(doc, items)
    collisions = [p for p in plans if p["problems"] and not p["item"].get("allow_overlap")]

    previews = []
    if preview:
        base, ext = os.path.splitext(preview)
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
            target = "%s-p%d%s" % (base, pno + 1, ext)
            page.get_pixmap(dpi=150).save(target)
            previews.append(target)
        doc.close()
        # preview must never be mistaken for a committed edit
        doc = pymupdf.open(path)
        if not guard_signed(doc, allow_signed, strip_signature, []):
            doc.close()
            return {"ok": False, "refused": True, "reason": "signed document", "notes": notes,
                    "placed": 0, "unplaced": [], "collisions": [], "plans": []}

    rows = _plan_rows(plans)
    coll_rows = [{"idx": p["idx"], "text": p["item"]["text"], "problems": p["problems"]}
                 for p in collisions]
    if missing or collisions:
        doc.close()
        return {"ok": False, "refused": False, "written": 0, "placed": 0, "unplaced": missing,
                "collisions": coll_rows, "plans": rows, "notes": notes, "previews": previews}
    if dry_run:
        doc.close()
        return {"ok": True, "refused": False, "dry_run": True, "placed": 0, "unplaced": [],
                "collisions": [], "plans": rows, "notes": notes, "previews": previews}

    for p in plans:
        doc[p["page"]].insert_text((p["x"], p["y"]), p["item"]["text"],
                                   fontname="helv", fontsize=p["size"], color=(0, 0, 0))
    placed = len(plans)
    doc.save(out, garbage=3, deflate=True)
    doc.close()

    with pymupdf.open(out) as check:
        blob = " ".join(p.get_text() for p in check)
    absent = [it["text"] for it in items if it["text"] and it["text"] not in blob]
    return {"ok": not absent, "refused": False, "placed": placed, "unplaced": [],
            "absent": absent, "collisions": [], "plans": rows, "notes": notes,
            "previews": previews, "output": str(out),
            "warned_overlaps": [{"idx": p["idx"], "text": p["item"]["text"],
                                 "problems": p["problems"]} for p in plans if p["problems"]]}


def cmd_fill(args):
    with open(args.map, encoding="utf-8-sig") as f:
        items = json.load(f)
    res = fill_map(args.pdf, items, args.out, dry_run=args.dry_run,
                   allow_signed=args.allow_signed, strip_signature=args.strip_signature,
                   preview=args.preview)
    for n in res["notes"]:
        print("  %s" % n)
    if res.get("refused"):
        print("REFUSED: %s" % res["reason"])
        return 3
    for target in res.get("previews", []):
        print("  preview -> %s" % target)
    for p in res["plans"]:
        tag = "OK  " if not p["problems"] else ("WARN" if p["allow_overlap"] else "BAD ")
        print("  %s #%-2d %-28r p%d at (%.0f, %.0f) size %.1f w=%.0f"
              % (tag, p["idx"], p["text"][:28], p["page"], p["x"], p["y"], p["size"], p["width"]))
        for prob in p["problems"]:
            print("            %s %s" % ("(allowed)" if p["allow_overlap"] else "->", prob))
        for note in p["notes"]:
            print("            NOTE %s" % note)
    if res["unplaced"]:
        print("\nUNPLACED:")
        for m in res["unplaced"]:
            print("  %s" % m)
    if res["collisions"]:
        print("\nPLACEMENT COLLISIONS (%d) - refusing to write. Adjust dx/dy/size/"
              "occurrence, or set \"allow_overlap\": true on an item that is meant to "
              "overprint." % len(res["collisions"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    if res["unplaced"] or res["collisions"]:
        print("\nVERIFY FAILED  (0 written, %d unplaced, %d colliding)"
              % (len(res["unplaced"]), len(res["collisions"])))
        return 2
    if res.get("dry_run"):
        print("\nDRY RUN  (%d placement(s) validated, nothing written)" % len(res["plans"]))
        return 0
    if res.get("absent"):
        print("\nNOT FOUND IN OUTPUT TEXT (drawn but unextractable): %s"
              % ", ".join(repr(a) for a in res["absent"]))
    print("\n%s  (%d placed, 0 unplaced, %d unverified)"
          % ("CLEAN" if res["ok"] else "VERIFY FAILED", res["placed"], len(res.get("absent", []))))
    return 0 if res["ok"] else 2


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
