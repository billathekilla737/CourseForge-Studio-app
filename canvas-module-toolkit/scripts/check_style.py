#!/usr/bin/env python3
"""
check_style.py - deterministic HTML compliance checker for restyled Canvas bodies.

Checks structural / accessibility rules that are true regardless of which school's
palette you use (heading structure, forbidden Canvas-sanitizer-stripped tags, alt
text, table headers, link text, ASCII safety). Palette conformance is checked ONLY
if you pass --palette, since every institution's brand colors differ - this script
ships with no hardcoded assumption about what your colors should be.

Usage:
    python3 check_style.py path/to/body.html
    python3 check_style.py path/to/body.html --palette references/palette.txt

--palette file format: one #rrggbb hex per line (case-insensitive), '#' comments ok.

Exit code 0 if all checks pass, 1 if any fail - designed to be read by a script or
an agent's dry-run gate, not re-derived by reasoning about the HTML by eye.
"""
import argparse
import re
import sys

SAFE_TAGS = {"div", "h2", "h3", "p", "ul", "li", "a", "strong", "span", "img",
             "table", "tr", "th", "td"}


def load_palette(path):
    """
    One #rrggbb per line. A line is a hex entry if it CONTAINS a 6-digit hex color
    ANYWHERE in it - do not try to strip '#'-comments first, since a hex color also
    starts with '#' and a naive "strip everything after the first #" approach deletes
    every real color line, silently emptying the palette (this shipped broken once;
    keep it simple on purpose so it doesn't happen again).
    """
    if not path:
        return None
    hexes = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"#[0-9a-fA-F]{6}\b", line)
            if m:
                hexes.add(m.group(0).lower())
    return hexes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file")
    ap.add_argument("--palette", default=None, help="optional file of allowed #hex colors, one per line")
    ap.add_argument("--max-alt-len", type=int, default=110)
    args = ap.parse_args()

    h = open(args.html_file, encoding="utf-8").read()
    passed, failed = [], []

    def ok(msg):
        passed.append(msg)
        print("  PASS  " + msg)

    def bad(msg):
        failed.append(msg)
        print("  FAIL  " + msg)

    def warn(msg):
        print("  WARN  " + msg)

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

    # --- nested <ul> (a <ul> opened before the previous one closes) ---
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

    # --- images: descriptive, concise alt text, never a bare filename ---
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
            elif len(m.group(1)) > args.max_alt_len:
                bad("img alt too long (%d chars, max %d)" % (len(m.group(1)), args.max_alt_len))
            elif m.group(1).strip() == "":
                ok("img alt=\"\" (marked decorative)")
            else:
                ok('img alt ok (%d chars): "%s"' % (len(m.group(1)), m.group(1)[:60]))

    # --- links: real text, and not color-alone (must carry underline) ---
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

    # --- ASCII only (avoids mangled entities on older PowerShell / ANSI-read hosts) ---
    nonascii = sorted(set(c for c in h if ord(c) >= 128))
    if not nonascii:
        ok("pure ASCII source (entity-encode non-ASCII characters)")
    else:
        warn("non-ASCII characters present: %r (fine if your pipeline writes/reads UTF-8 consistently)" % nonascii[:12])

    # --- palette conformance (only if the caller supplied one) ---
    palette = load_palette(args.palette)
    hexes = set(x.lower() for x in re.findall(r"(?<!&)#[0-9a-fA-F]{6}\b", h))
    if palette is None:
        if hexes:
            print("  ....  %d color(s) found; pass --palette to check them against your brand list" % len(hexes))
    else:
        off = hexes - palette
        if not off:
            ok("all %d color(s) are in the supplied palette" % len(hexes))
        else:
            bad("off-palette colors: %s" % ", ".join(sorted(off)))

    print()
    print("RESULT: %d passed, %d failed" % (len(passed), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
