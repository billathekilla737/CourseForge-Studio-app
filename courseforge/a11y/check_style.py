"""Deterministic HTML compliance checker for restyled Canvas bodies (ported
from the old toolkit's check_style.py).

Checks structural and accessibility rules that hold regardless of palette:
heading structure, constructs the Canvas sanitizer strips, alt text, table
headers, link text, ASCII safety. Palette conformance is checked only when a
palette is supplied, because every institution's brand differs.

    check(html, palette=None, max_alt_len=110) -> {"passed", "failed", "warned", "ok"}
    load_palette(path) -> set of "#rrggbb"
    brand_palette()    -> the set from brand.json (what the restyler writes)

CLI:
    python -m courseforge.a11y.check_style body.html [--palette palette.txt] [--brand]
Exit 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys

from . import restyle

SAFE_TAGS = {"div", "h2", "h3", "p", "ul", "li", "a", "strong", "span", "img",
             "table", "tr", "th", "td"}


def load_palette(path):
    """One #rrggbb per line. A line is a hex entry if it CONTAINS a 6-digit hex
    color anywhere in it. Do not strip '#'-comments first: a hex color also
    starts with '#', and doing so once emptied every palette silently."""
    if not path:
        return None
    hexes = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"#[0-9a-fA-F]{6}\b", line)
            if m:
                hexes.add(m.group(0).lower())
    return hexes


def brand_palette() -> set:
    return {v.lower() for v in restyle.C.values()
            if isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}$", v)}


def check(h: str, palette=None, max_alt_len: int = 110) -> dict:
    passed, failed, warned = [], [], []
    ok, bad, warn = passed.append, failed.append, warned.append
    h = h or ""

    # --- heading structure ---
    h2 = re.findall(r"<h2\b", h, re.I)
    h3 = re.findall(r"<h3\b", h, re.I)
    deeper = re.findall(r"<h[1456]\b", h, re.I)
    if len(h2) == 1:
        ok("exactly one <h2> (reserve it for the page/lesson title)")
    else:
        bad("found %d <h2> (expected exactly 1)" % len(h2))
    if h3:
        ok("%d <h3> section heading(s)" % len(h3))
    else:
        warn("no <h3> sections found")
    if not deeper:
        ok("no h1/h4/h5/h6 (no skipped heading levels)")
    else:
        bad("found skipped/extra heading levels: %s" % sorted(set(x.lower() for x in deeper)))

    # --- constructs the Canvas Rich Content Editor sanitizer strips or that hurt a11y ---
    forbidden = [
        ("<ol>", r"<ol\b", "no <ol> (Canvas strips it; number list items inline instead)"),
        ("<br>", r"<br\b", "no <br> (Canvas strips it; use margin-top spacing instead)"),
        ("<em>", r"<em\b", "no <em> (Canvas strips it; use a span with font-style: italic)"),
        ("<style>", r"<style\b", "no <style> block (stripped)"),
        ("<hr>", r"<hr\b", "no <hr> (stripped; use card borders for separation)"),
        ("box-shadow", r"box-shadow", "no box-shadow (stripped)"),
        ("web-font <link>", r"<link\b", "no web-font <link> (stripped)"),
        ("HTML comment", r"<!--", "no HTML comments (stripped)"),
        ("<script>", r"<script\b", "no <script> (stripped; Canvas injects its own theme script)"),
    ]
    for label, rx, desc in forbidden:
        if re.search(rx, h, re.I):
            bad("%s present - %s" % (label, desc))
        else:
            ok(desc)

    classes = [c for c in re.findall(r'class\s*=\s*"([^"]*)"', h, re.I)
               if "instructure_file_link" not in c]
    if classes:
        bad("styling class= present (stripped by Canvas anyway): %s" % classes)
    else:
        ok("no styling class= attributes")
    if re.findall(r'\sid\s*=\s*"', h, re.I):
        bad("id= attribute(s) present (unnecessary; not needed for inline styling)")
    else:
        ok("no id= attributes")

    # --- nested <ul> ---
    depth = maxdepth = 0
    for m in re.finditer(r"<(/?)ul\b", h, re.I):
        depth += -1 if m.group(1) else 1
        maxdepth = max(maxdepth, depth)
    if maxdepth <= 1:
        ok("no nested <ul> (max list depth %d)" % maxdepth)
    else:
        bad("nested <ul> found (depth %d) - Canvas sanitizer mangles these" % maxdepth)

    # --- only safe tags ---
    used = set(t.lower() for t in re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)", h))
    extra = used - SAFE_TAGS
    if not extra:
        ok("only safe tags used: %s" % ", ".join(sorted(used)))
    else:
        bad("non-standard tags present (verify Canvas keeps these): %s" % ", ".join(sorted(extra)))

    # --- tables carry scope ---
    ths = re.findall(r"<th\b([^>]*)>", h, re.I)
    has_table = re.search(r"<table\b", h, re.I)
    if ths:
        noscope = [t for t in ths if "scope=" not in t.lower()]
        if noscope:
            bad("%d <th> without scope=" % len(noscope))
        else:
            ok("all %d <th> have scope= (%d col, %d row)" % (
                len(ths),
                sum(1 for t in ths if 'scope="col"' in t.lower()),
                sum(1 for t in ths if 'scope="row"' in t.lower())))
    elif has_table:
        bad("table present with no <th> at all")
    else:
        ok("no tables (none needed)")

    # --- images ---
    imgs = re.findall(r"<img\b([^>]*)>", h, re.I)
    if not imgs:
        ok("no images (no alt-text risk)")
    else:
        for a in imgs:
            m = re.search(r'alt\s*=\s*"([^"]*)"', a, re.I)
            if not m:
                bad("<img> with no alt attribute at all")
            elif re.search(r"\.(png|jpe?g|gif|webp|bmp|svg)$", m.group(1).strip(), re.I):
                bad("img alt is a bare filename: %r" % m.group(1))
            elif len(m.group(1)) > max_alt_len:
                bad("img alt too long (%d chars, max %d)" % (len(m.group(1)), max_alt_len))
            elif m.group(1).strip() == "":
                ok("img alt=\"\" (marked decorative)")
            else:
                ok('img alt ok (%d chars): "%s"' % (len(m.group(1)), m.group(1)[:60]))

    # --- links ---
    links = re.findall(r"<a\b([^>]*)>(.*?)</a>", h, re.I | re.S)
    if not links:
        ok("no links (none required)")
    else:
        for attrs, text in links:
            clean = re.sub(r"<[^>]+>", "", text).strip()
            if not clean:
                bad("link with no visible text")
            elif "underline" not in attrs.lower():
                warn("link may rely on color alone (no underline found): %s" % clean[:40])
            else:
                ok("link ok: %s" % clean[:40])

    # --- ASCII only ---
    nonascii = sorted(set(c for c in h if ord(c) >= 128))
    if not nonascii:
        ok("pure ASCII source (entity-encode non-ASCII characters)")
    else:
        warn("non-ASCII characters present: %r (fine if your pipeline writes/reads UTF-8 consistently)" % nonascii[:12])

    # --- palette conformance (only if the caller supplied one) ---
    hexes = set(x.lower() for x in re.findall(r"(?<!&)#[0-9a-fA-F]{6}\b", h))
    if palette is None:
        if hexes:
            warn("%d color(s) found; pass a palette to check them against your brand list" % len(hexes))
    else:
        off = hexes - set(p.lower() for p in palette)
        if not off:
            ok("all %d color(s) are in the supplied palette" % len(hexes))
        else:
            bad("off-palette colors: %s" % ", ".join(sorted(off)))

    return {"passed": passed, "failed": failed, "warned": warned, "ok": not failed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file")
    ap.add_argument("--palette", default=None, help="optional file of allowed #hex colors, one per line")
    ap.add_argument("--brand", action="store_true", help="check colors against brand.json")
    ap.add_argument("--max-alt-len", type=int, default=110)
    args = ap.parse_args(argv)
    with open(args.html_file, encoding="utf-8") as f:
        h = f.read()
    palette = load_palette(args.palette) if args.palette else (brand_palette() if args.brand else None)
    result = check(h, palette, args.max_alt_len)
    for line in result["passed"]:
        print("  PASS  " + line)
    for line in result["warned"]:
        print("  WARN  " + line)
    for line in result["failed"]:
        print("  FAIL  " + line)
    print()
    print("RESULT: %d passed, %d failed" % (len(result["passed"]), len(result["failed"])))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
