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
  python -m courseforge.docs.office_text scan  f1.docx [f2.pptx ...] --pattern "regex" [--json out]
  python -m courseforge.docs.office_text apply in.docx --map map.json --out fixed.docx
        map.json: [ {"find": "Jane Smith", "replace": "John Doe"}, ... ]
        options: --set-author X  --set-lastmodifiedby X  [--json report]

Library surface (what the Studio's gateway calls; nothing here prints):
  scan_file(path, pattern) -> dict         {"kind", "hits", "unsupported", "parts"}
  apply_map(path, mappings, out, set_author=None, set_lastmodifiedby=None) -> dict
  verify_file(out, mappings) -> dict       {"ok", "residual", "zip_ok"}

EXIT CODES  0 ok | 1 usage/IO | 2 verification failed | 4 unsupported format
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

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
    except OSError:
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


def _normalize(mappings):
    """Accept [{find, replace}] or {find: replace}; drop empty finds."""
    if isinstance(mappings, dict):
        mappings = [{"find": k, "replace": v} for k, v in mappings.items()]
    out = []
    for m in mappings or []:
        find = (m.get("find") or "")
        if not find:
            continue
        out.append({"find": find, "replace": m.get("replace") or ""})
    return out


# ---------- scan ----------

def scan_file(path, pattern):
    """Regex search over every text part of one OOXML file. Read-only."""
    kind = classify(path)
    res = {"file": str(path), "kind": kind, "hits": [], "unsupported": kind != "ooxml",
           "parts": 0, "pattern": pattern}
    if kind == "legacy":
        res["reason"] = ("legacy binary Office format; convert to .docx/.pptx/.xlsx "
                         "first (File > Save As)")
        return res
    if kind != "ooxml":
        res["reason"] = "not an OOXML container"
        return res
    rx = re.compile(pattern, re.I)
    with zipfile.ZipFile(path) as zf:
        for item, text, _raw in iter_text_parts(zf):
            if text is None:
                continue
            res["parts"] += 1
            for m in rx.finditer(text):
                s = max(0, m.start() - 70)
                res["hits"].append({"part": item.filename, "term": m.group(0),
                                    "context": re.sub(r"\s+", " ", text[s:m.end() + 70])})
    return res


def cmd_scan(args):
    hits, unsupported = [], []
    for path in args.files:
        res = scan_file(path, args.pattern)
        if res["kind"] == "legacy":
            unsupported.append(path)
            print("UNSUPPORTED %s - legacy binary Office format; convert to "
                  ".docx/.pptx/.xlsx first (File > Save As)." % path)
            continue
        if res["kind"] != "ooxml":
            print("SKIP        %s - not an OOXML container." % path)
            continue
        for h in res["hits"]:
            hits.append({"file": path, **h})
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


# ---------- apply ----------

def set_core_prop(text, tag, value):
    """Set <dc:creator> / <cp:lastModifiedBy> in core.xml text, creating if empty."""
    pat = re.compile(r"(<%s[^>]*>)(.*?)(</%s>)" % (tag, tag), re.S)
    if pat.search(text):
        return pat.sub(lambda m: m.group(1) + xml_escape(value) + m.group(3), text), True
    return text, False


def verify_file(out, mappings):
    """Archive intact and zero residual across every text part."""
    mappings = _normalize(mappings)
    residual = {m["find"]: 0 for m in mappings}
    try:
        zv = zipfile.ZipFile(out)
    except zipfile.BadZipFile as exc:
        return {"ok": False, "zip_ok": False, "residual": residual,
                "problems": ["output is not a zip: %s" % exc]}
    with zv:
        bad_entry = zv.testzip()
        for item, text, _raw in iter_text_parts(zv):
            if text is None:
                continue
            for m in mappings:
                for variant in find_variants(m["find"]):
                    residual[m["find"]] += len(re.findall(re.escape(variant), text, re.I))
    problems = []
    if bad_entry is not None:
        problems.append("zip integrity failed at %r" % bad_entry)
    left = {k: v for k, v in residual.items() if v}
    if left:
        problems.append("text still present: " + ", ".join("%r x%d" % kv for kv in list(left.items())[:6]))
    name = os.path.basename(str(out)).lower()
    try:
        if name.endswith(".docx"):
            from docx import Document
            Document(str(out))
        elif name.endswith(".pptx"):
            from pptx import Presentation
            Presentation(str(out))
    except Exception as exc:  # noqa: BLE001
        problems.append("file does not open as an Office document: %s" % exc)
    return {"ok": not problems, "zip_ok": bad_entry is None, "residual": residual,
            "problems": problems}


def apply_map(path, mappings, out, set_author=None, set_lastmodifiedby=None):
    """Replace every mapping across every text part, write `out`, verify it."""
    kind = classify(path)
    if kind != "ooxml":
        reason = ("legacy binary Office format; convert to OOXML first"
                  if kind == "legacy" else "not an OOXML container")
        return {"ok": False, "refused": True, "reason": reason, "kind": kind,
                "replacements": {}, "residual": {}, "parts_changed": {},
                "unmatched_mappings": [], "zip_ok": False, "notes": []}
    mappings = _normalize(mappings)
    replaced = {m["find"]: 0 for m in mappings}
    changed_parts = {}
    notes = []
    buf = io.BytesIO()
    # single pass over EVERY part: binary parts copy through byte-for-byte,
    # text parts get the replacements. (A second bookkeeping pass over the
    # half-written archive is unreadable mid-write - central directory only
    # exists after close - and cost a real crash before this comment existed.)
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
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
                if set_author is not None:
                    text, ok = set_core_prop(text, "dc:creator", set_author)
                    changed_parts.setdefault(item.filename, 0)
                    if not ok:
                        notes.append("no <dc:creator> element to set in core.xml")
                if set_lastmodifiedby is not None:
                    text, ok = set_core_prop(text, "cp:lastModifiedBy", set_lastmodifiedby)
                    changed_parts.setdefault(item.filename, 0)
                    if not ok:
                        notes.append("no <cp:lastModifiedBy> element to set in core.xml")
            if total:
                changed_parts[item.filename] = changed_parts.get(item.filename, 0) + total
            zout.writestr(item, text.encode("utf-8"))
    with open(out, "wb") as f:
        f.write(buf.getvalue())

    ver = verify_file(out, mappings)
    never = [k for k, v in replaced.items() if v == 0]
    return {"ok": ver["ok"], "refused": False, "input": str(path), "output": str(out),
            "replacements": replaced, "residual": ver["residual"],
            "parts_changed": changed_parts, "unmatched_mappings": never,
            "zip_ok": ver["zip_ok"], "problems": ver["problems"], "notes": notes,
            "total": sum(replaced.values())}


def cmd_apply(args):
    with open(args.map, encoding="utf-8-sig") as f:
        mappings = json.load(f)
    res = apply_map(args.file, mappings, args.out,
                    set_author=args.set_author, set_lastmodifiedby=args.set_lastmodifiedby)
    if res.get("refused"):
        print("UNSUPPORTED: %s." % res["reason"])
        return 4
    for note in res["notes"]:
        print("  note: %s" % note)
    for k, v in res["replacements"].items():
        left = res["residual"].get(k, 0)
        flag = "" if left == 0 else "   RESIDUAL=%d !!" % left
        print("%-52s x%d%s" % (k[:52], v, flag))
    if res["unmatched_mappings"]:
        print("NEVER MATCHED (word may have split the text across runs - check with "
              "`scan`): %s" % ", ".join(repr(n) for n in res["unmatched_mappings"]))
    if not res["zip_ok"]:
        print("ZIP INTEGRITY FAILED")
    for p, n in sorted(res["parts_changed"].items()):
        print("  part %-40s %d change(s)" % (p, n))
    print("\n%s  (%d replacement(s), %d residual)"
          % ("CLEAN" if res["ok"] else "VERIFY FAILED", res["total"],
             sum(res["residual"].values())))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0 if res["ok"] else 2


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
