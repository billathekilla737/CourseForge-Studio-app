"""office_text_tool.py (courseforge) - Office (OOXML) text SCAN and targeted EDIT.

Generalizes the one-off cleanup that removed a former instructor's name from a
course's .docx/.pptx/.xlsx files into a reusable tool. OOXML files are ZIP
containers of XML parts; working on the raw XML catches places library-level
APIs miss: docProps/core.xml (Author / Last Modified By), slide masters and
layouts, headers/footers, notes, and comments.

SUBCOMMANDS
  scan   regex search over every XML part of each file (binary parts skipped).
         Reports file, internal part, and context. Legacy binary Office files
         (.doc/.ppt/.xls, OLE magic) are flagged UNSUPPORTED, never silently
         skipped. Read-only.
  apply  replace literal strings across all XML parts. Replacement text is
         XML-escaped automatically; when a find string contains & < > it is
         ALSO matched in its XML-escaped form. Verifies afterward: the output
         must contain zero occurrences of any find string, and the rewritten
         archive must pass a zip integrity check.

HONEST SCOPE
  - Matching is literal, against the raw XML. Word frequently SPLITS a run of
    text across multiple <w:t>/<a:t> elements (spell-check marks, formatting
    changes mid-phrase), and a split phrase will NOT match. `scan` sees exactly
    what `apply` sees, so scan first and copy exact strings; a mapping that
    never matches is reported, not ignored.
  - This edits character data. It does not restructure documents, fix styles,
    or do accessibility work (that is remediate_docx.py / remediate_pptx.py).
  - Legacy .doc/.ppt/.xls cannot be edited - convert to OOXML first (Word:
    File > Save As > .docx). The tool says so per file.

USAGE
  python office_text_tool.py scan  f1.docx [f2.pptx ...] --pattern "regex" [--json out]
  python office_text_tool.py apply in.docx --map map.json --out fixed.docx
        map.json: [ {"find": "Jane Smith", "replace": "John Doe"}, ... ]
        options: --set-author X  --set-lastmodifiedby X  [--json report]

EXIT CODES  0 ok | 1 usage/IO | 2 verification failed | 4 unsupported format
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".emf", ".wmf", ".bin", ".bmp",
              ".tiff", ".tif", ".mp3", ".mp4", ".wav", ".m4a", ".ttf", ".otf",
              ".jpe", ".ico", ".pdf"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # legacy .doc/.ppt/.xls
CORE_PROPS = "docProps/core.xml"


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def find_variants(find):
    """The literal find string, plus its XML-escaped form when they differ."""
    esc = xml_escape(find)
    return [find] if esc == find else [find, esc]


def classify(path):
    """'ooxml' | 'legacy' | 'other' - never guess from extension alone."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        print("UNREADABLE  %s  (%s)" % (path, e))
        return "other"
    if head[:8] == OLE_MAGIC:
        return "legacy"
    if head[:2] == b"PK" and zipfile.is_zipfile(path):
        return "ooxml"
    return "other"


def iter_text_parts(zf):
    for item in zf.infolist():
        if os.path.splitext(item.filename)[1].lower() in BINARY_EXT:
            continue
        data = zf.read(item.filename)
        try:
            yield item, data.decode("utf-8"), data
        except UnicodeDecodeError:
            yield item, None, data


def cmd_scan(args):
    rx = re.compile(args.pattern, re.I)
    hits, unsupported = [], []
    for path in args.files:
        kind = classify(path)
        if kind == "legacy":
            unsupported.append(path)
            print("UNSUPPORTED %s - legacy binary Office format; convert to "
                  ".docx/.pptx/.xlsx first (File > Save As)." % path)
            continue
        if kind != "ooxml":
            print("SKIP        %s - not an OOXML container." % path)
            continue
        zf = zipfile.ZipFile(path)
        for item, text, _raw in iter_text_parts(zf):
            if text is None:
                continue
            for m in rx.finditer(text):
                s = max(0, m.start() - 70)
                hits.append({"file": path, "part": item.filename, "term": m.group(0),
                             "context": re.sub(r"\s+", " ", text[s:m.end() + 70])})
        zf.close()
    for h in hits:
        print("HIT  %-30s %-34s %-18s ...%s..." %
              (os.path.basename(h["file"])[:30], h["part"][:34],
               h["term"][:18], h["context"][:70]))
    print("\n%d hit(s) across %d file(s); %d unsupported legacy file(s)"
          % (len(hits), len(args.files), len(unsupported)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"hits": hits, "unsupported": unsupported}, f, indent=1)
    return 4 if (unsupported and not hits) else 0


def set_core_prop(text, tag, value):
    """Set <dc:creator> / <cp:lastModifiedBy> in core.xml text, creating if empty."""
    pat = re.compile(r"(<%s[^>]*>)(.*?)(</%s>)" % (tag, tag), re.S)
    if pat.search(text):
        return pat.sub(lambda m: m.group(1) + xml_escape(value) + m.group(3), text), True
    return text, False


def cmd_apply(args):
    kind = classify(args.file)
    if kind == "legacy":
        print("UNSUPPORTED: legacy binary Office format - convert to OOXML first.")
        return 4
    if kind != "ooxml":
        print("UNSUPPORTED: not an OOXML container.")
        return 4
    with open(args.map, encoding="utf-8-sig") as f:
        mappings = json.load(f)

    zin = zipfile.ZipFile(args.file)
    replaced = {m["find"]: 0 for m in mappings}
    changed_parts = {}
    out = io.BytesIO()
    # single pass over EVERY part: binary parts copy through byte-for-byte,
    # text parts get the replacements. (A second bookkeeping pass over the
    # half-written archive is unreadable mid-write - central directory only
    # exists after close - and cost a real crash before this comment existed.)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if os.path.splitext(item.filename)[1].lower() in BINARY_EXT:
                zout.writestr(item, raw)
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                zout.writestr(item, raw)
                continue
            total = 0
            for m in mappings:
                repl = xml_escape(m["replace"])
                for variant in find_variants(m["find"]):
                    text, n = re.subn(re.escape(variant), repl, text, flags=re.I)
                    total += n
                    replaced[m["find"]] += n
            if item.filename == CORE_PROPS:
                if args.set_author is not None:
                    text, ok = set_core_prop(text, "dc:creator", args.set_author)
                    changed_parts.setdefault(item.filename, 0)
                    if not ok:
                        print("  note: no <dc:creator> element to set in core.xml")
                if args.set_lastmodifiedby is not None:
                    text, ok = set_core_prop(text, "cp:lastModifiedBy", args.set_lastmodifiedby)
                    changed_parts.setdefault(item.filename, 0)
                    if not ok:
                        print("  note: no <cp:lastModifiedBy> element to set in core.xml")
            if total:
                changed_parts[item.filename] = changed_parts.get(item.filename, 0) + total
            zout.writestr(item, text.encode("utf-8"))
    zin.close()
    with open(args.out, "wb") as f:
        f.write(out.getvalue())

    # verify: archive intact and zero residual across every text part
    zv = zipfile.ZipFile(args.out)
    bad_entry = zv.testzip()
    residual = {m["find"]: 0 for m in mappings}
    for item, text, _raw in iter_text_parts(zv):
        if text is None:
            continue
        for m in mappings:
            for variant in find_variants(m["find"]):
                residual[m["find"]] += len(re.findall(re.escape(variant), text, re.I))
    zv.close()

    for k, v in replaced.items():
        flag = "" if residual[k] == 0 else "   RESIDUAL=%d !!" % residual[k]
        print("%-52s x%d%s" % (k[:52], v, flag))
    never = [k for k, v in replaced.items() if v == 0]
    if never:
        print("NEVER MATCHED (word may have split the text across runs - check with "
              "`scan`): %s" % ", ".join(repr(n) for n in never))
    if bad_entry is not None:
        print("ZIP INTEGRITY FAILED at %r" % bad_entry)
    for p, n in sorted(changed_parts.items()):
        print("  part %-40s %d change(s)" % (p, n))
    bad = sum(residual.values()) + (1 if bad_entry else 0)
    print("\n%s  (%d replacement(s), %d residual)"
          % ("CLEAN" if bad == 0 else "VERIFY FAILED", sum(replaced.values()),
             sum(residual.values())))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"input": args.file, "output": args.out, "replacements": replaced,
                       "residual": residual, "parts_changed": changed_parts,
                       "unmatched_mappings": never, "zip_ok": bad_entry is None},
                      f, indent=1)
    return 0 if bad == 0 else 2


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("files", nargs="+")
    s.add_argument("--pattern", required=True)
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scan)
    a = sub.add_parser("apply")
    a.add_argument("file")
    a.add_argument("--map", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--set-author", default=None)
    a.add_argument("--set-lastmodifiedby", default=None)
    a.add_argument("--json")
    a.set_defaults(fn=cmd_apply)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
