"""Deterministic checker for Canvas-safe HTML bodies (library and CLI).

Ported from the old toolkit's check_style.py. The rules are the ones the Canvas
Rich Content Editor sanitizer and Anthology Ally actually enforce: one <h2>
then <h3> sections with no skipped level, no constructs the sanitizer strips
(<ol>, <br>, <em>, <style>, <script>, class=, id=, nested <ul>, box-shadow),
descriptive alt text that is never a filename, <th scope> on real tables, links
with text and an underline, pure ASCII source, and WCAG 4.5:1 contrast on any
text whose colour and background are both set inline.

Palette conformance runs only when a palette is supplied (brand.json gives one),
because every institution's colours differ.

    from courseforge.content import check_style
    report = check_style.check(html, palette=check_style.brand_palette())
    report["ok"]        # True when nothing failed
    report["failed"]    # list of sentences
    report["chips"]     # [{id, label, state, detail}] for the review screen

    python -m courseforge.content.check_style body.html [--palette file]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

SAFE_TAGS = {"div", "h2", "h3", "p", "ul", "li", "a", "strong", "span", "img",
             "table", "tr", "th", "td", "thead", "tbody"}

FORBIDDEN = [
    ("<ol>", r"<ol\b", "no <ol> (Canvas strips it; number list items inline instead)"),
    ("<br>", r"<br\b", "no <br> (Canvas strips it; use margin-top spacing instead)"),
    ("<em>", r"<em\b", "no <em> (Canvas strips it; use a span with font-style: italic)"),
    ("<style>", r"<style\b", "no <style> block (stripped)"),
    ("<hr>", r"<hr\b", "no <hr> (stripped; use card borders for separation)"),
    ("box-shadow", r"box-shadow", "no box-shadow (stripped)"),
    ("web-font <link>", r"<link\b", "no web-font <link> (stripped)"),
    ("HTML comment", r"<!--", "no HTML comments (stripped)"),
    ("<script>", r"<script\b", "no <script> (stripped; Canvas injects its own theme script)"),
    ("<iframe>", r"<iframe\b", "no <iframe> (embed a link instead; external content is the instructor's call)"),
]

# Canvas renders a body in dark text on white unless the markup says otherwise.
DEFAULT_FG = "#2d3b45"
DEFAULT_BG = "#ffffff"
CONTRAST_NEED = 4.5

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


# ------------------------------------------------------------------ palette
def brand_palette(path: str | Path | None = None) -> set[str]:
    """Every colour in brand.json, plus the style guide's fixed accents that are
    not brand choices (the pill fill, the alert border, the safe link blue)."""
    import os
    candidates = [path, os.environ.get("CF_BRAND"),
                  Path(__file__).resolve().parent.parent / "brand.json"]
    hexes: set[str] = set()
    for cand in candidates:
        if not cand:
            continue
        try:
            data = json.loads(Path(cand).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for value in (data.get("colors") or {}).values():
            m = _HEX.search(str(value))
            if m:
                hexes.add(_norm_hex(m.group(0)))
        break
    hexes.update({"#0e2c54", "#f3c2c8", "#1565c0", "#186fc8", "#fff8e6", "#000000"})
    return hexes


def load_palette_file(path: str | Path) -> set[str]:
    """One #rrggbb per line; a line counts if it contains a hex anywhere."""
    hexes: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.search(r"#[0-9a-fA-F]{6}\b", line)
        if m:
            hexes.add(m.group(0).lower())
    return hexes


def _norm_hex(value: str) -> str:
    value = value.strip().lower()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


# ----------------------------------------------------------------- contrast
def luminance(hexcolor: str) -> float:
    h = _norm_hex(hexcolor).lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    rgb = [(c / 12.92) if c <= 0.04045 else (((c + 0.055) / 1.055) ** 2.4) for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast_ratio(fg: str, bg: str) -> float:
    la, lb = luminance(fg), luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_COLOR_PROP = re.compile(r"(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{3,6})", re.I)
_BG_PROP = re.compile(r"(?:^|;)\s*background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,6})", re.I)


class _ContrastWalk(HTMLParser):
    """Track the nearest inherited colour and background for every text run."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str, str]] = []
        self.failures: list[str] = []
        self.checked = 0
        self._seen: set[tuple[str, str]] = set()

    def _current(self) -> tuple[str, str]:
        fg, bg = DEFAULT_FG, DEFAULT_BG
        if self.stack:
            _, fg, bg = self.stack[-1]
        return fg, bg

    def handle_starttag(self, tag, attrs):
        fg, bg = self._current()
        style = dict(attrs).get("style") or ""
        m = _COLOR_PROP.search(style)
        if m:
            fg = _norm_hex(m.group(1))
        m = _BG_PROP.search(style)
        if m:
            bg = _norm_hex(m.group(1))
        if tag not in ("img", "br", "hr", "link", "meta", "input"):
            self.stack.append((tag, fg, bg))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data.strip():
            return
        fg, bg = self._current()
        if (fg, bg) in self._seen:
            return
        self._seen.add((fg, bg))
        self.checked += 1
        ratio = contrast_ratio(fg, bg)
        if ratio < CONTRAST_NEED:
            snippet = " ".join(data.split())[:40]
            self.failures.append(
                f"text {fg} on {bg} is {ratio:.2f}:1 (needs {CONTRAST_NEED}:1): \"{snippet}\"")


# -------------------------------------------------------------------- check
def check(html: str, palette: set[str] | None = None, max_alt_len: int = 110,
          ascii_fails: bool = False) -> dict:
    """Run every rule. Returns {ok, passed, failed, warned, chips, counts}."""
    h = html or ""
    passed: list[str] = []
    failed: list[str] = []
    warned: list[str] = []
    chips: list[dict] = []

    def ok(msg):
        passed.append(msg)

    def bad(msg):
        failed.append(msg)

    def warn(msg):
        warned.append(msg)

    def chip(cid, label, state, detail=""):
        chips.append({"id": cid, "label": label, "state": state, "detail": detail})

    # --- heading structure -------------------------------------------------
    headings = [int(m.group(1)) for m in re.finditer(r"<h([1-6])\b", h, re.I)]
    h2_count = headings.count(2)
    h3_count = headings.count(3)
    if h2_count == 1:
        ok("exactly one <h2> (the page title)")
        chip("one_h2", "One h2", "pass")
    else:
        bad(f"found {h2_count} <h2> (expected exactly 1)")
        chip("one_h2", "One h2", "fail", f"{h2_count} found")
    if h3_count:
        ok(f"{h3_count} <h3> section heading(s)")
    else:
        warn("no <h3> sections found")
    order_problem = ""
    if headings and headings[0] != 2:
        order_problem = f"first heading is h{headings[0]}, not h2"
    if 1 in headings:
        order_problem = order_problem or "an <h1> is present (Canvas owns the only h1)"
    prev = 2
    for level in headings:
        if level > prev + 1:
            order_problem = order_problem or f"h{prev} jumps to h{level} (skipped level)"
        prev = level
    if order_problem:
        bad("heading order: " + order_problem)
        chip("heading_order", "Headings in order", "fail", order_problem)
    else:
        ok("headings in order, no skipped levels")
        chip("heading_order", "Headings in order", "pass")

    # --- sanitizer-stripped constructs ------------------------------------
    stripped: list[str] = []
    for label, rx, desc in FORBIDDEN:
        if re.search(rx, h, re.I):
            bad(f"{label} present: {desc}")
            stripped.append(label)
        else:
            ok(desc)
    classes = [c for c in re.findall(r'class\s*=\s*"([^"]*)"', h, re.I)
               if "instructure_file_link" not in c]
    if classes:
        bad(f"styling class= present (Canvas strips it): {classes[:5]}")
        stripped.append("class=")
    else:
        ok("no styling class= attributes")
    if re.findall(r'\sid\s*=\s*"', h, re.I):
        bad("id= attribute(s) present (stripped; not needed for inline styling)")
        stripped.append("id=")
    else:
        ok("no id= attributes")
    depth = maxdepth = 0
    for m in re.finditer(r"<(/?)ul\b", h, re.I):
        depth += -1 if m.group(1) else 1
        maxdepth = max(maxdepth, depth)
    if maxdepth <= 1:
        ok(f"no nested <ul> (max list depth {maxdepth})")
    else:
        bad(f"nested <ul> found (depth {maxdepth}); the sanitizer mangles these")
        stripped.append("nested <ul>")
    used = set(t.lower() for t in re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)", h))
    extra = used - SAFE_TAGS
    if not extra:
        ok("only safe tags used: " + ", ".join(sorted(used)))
    else:
        bad("tags outside the safe list: " + ", ".join(sorted(extra)))
        stripped.extend(sorted(extra))
    chip("sanitizer", "Sanitizer-safe", "fail" if stripped else "pass",
         ", ".join(stripped[:6]))

    # --- tables carry scope -----------------------------------------------
    ths = re.findall(r"<th\b([^>]*)>", h, re.I)
    has_table = bool(re.search(r"<table\b", h, re.I))
    if ths:
        noscope = [t for t in ths if "scope=" not in t.lower()]
        if noscope:
            bad(f"{len(noscope)} <th> without scope=")
            chip("tables", "Table headers", "fail", f"{len(noscope)} th without scope")
        else:
            ok(f"all {len(ths)} <th> have scope=")
            chip("tables", "Table headers", "pass")
    elif has_table:
        bad("table present with no <th> at all")
        chip("tables", "Table headers", "fail", "table without th")
    else:
        ok("no tables")

    # --- images -----------------------------------------------------------
    imgs = re.findall(r"<img\b([^>]*)>", h, re.I)
    alt_bad = 0
    if not imgs:
        ok("no images")
        chip("alt", "Alt present", "pass", "no images")
    else:
        for a in imgs:
            m = re.search(r'alt\s*=\s*"([^"]*)"', a, re.I)
            if not m:
                bad("<img> with no alt attribute at all")
                alt_bad += 1
            elif re.search(r"\.(png|jpe?g|gif|webp|bmp|svg)$", m.group(1).strip(), re.I):
                bad(f"img alt is a bare filename: {m.group(1)!r}")
                alt_bad += 1
            elif len(m.group(1)) > max_alt_len:
                bad(f"img alt too long ({len(m.group(1))} chars, max {max_alt_len})")
                alt_bad += 1
            elif m.group(1).strip() == "":
                ok('img alt="" (marked decorative)')
            else:
                ok(f"img alt ok ({len(m.group(1))} chars)")
        chip("alt", "Alt present", "fail" if alt_bad else "pass",
             f"{alt_bad} of {len(imgs)} images" if alt_bad else f"{len(imgs)} images")

    # --- links ------------------------------------------------------------
    links = re.findall(r"<a\b([^>]*)>(.*?)</a>", h, re.I | re.S)
    link_bad = 0
    for attrs, text in links:
        clean = re.sub(r"<[^>]+>", "", text).strip()
        if not clean:
            bad("link with no visible text")
            link_bad += 1
        elif "underline" not in attrs.lower():
            warn(f"link may rely on colour alone (no underline): {clean[:40]}")
        else:
            ok(f"link ok: {clean[:40]}")
    if links:
        chip("links", "Links have text", "fail" if link_bad else "pass")

    # --- ASCII ------------------------------------------------------------
    nonascii = sorted(set(c for c in h if ord(c) >= 128))
    if not nonascii:
        ok("pure ASCII source")
        chip("ascii", "ASCII only", "pass")
    else:
        shown = "".join(nonascii[:12])
        msg = f"non-ASCII characters present: {shown!r} (entity-encode them)"
        if ascii_fails:
            bad(msg)
        else:
            warn(msg)
        chip("ascii", "ASCII only", "fail" if ascii_fails else "warn", shown)

    # --- contrast ---------------------------------------------------------
    walk = _ContrastWalk()
    try:
        walk.feed(h)
        walk.close()
    except Exception as exc:  # noqa: BLE001
        warn(f"contrast walk skipped: {exc}")
    if walk.failures:
        for f in walk.failures:
            bad("contrast: " + f)
        chip("contrast", "Contrast 4.5:1", "fail", walk.failures[0][:80])
    else:
        ok(f"contrast ok on {walk.checked} colour pair(s)")
        chip("contrast", "Contrast 4.5:1", "pass")

    # --- palette ----------------------------------------------------------
    hexes = set(_norm_hex(x) for x in re.findall(r"(?<!&)#[0-9a-fA-F]{6}\b", h))
    if palette is not None and hexes:
        off = hexes - set(_norm_hex(p) for p in palette)
        if off:
            warn("off-palette colours: " + ", ".join(sorted(off)))
            chip("palette", "Brand palette", "warn", ", ".join(sorted(off))[:80])
        else:
            ok(f"all {len(hexes)} colour(s) are in the palette")
            chip("palette", "Brand palette", "pass")

    return {
        "ok": not failed,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "chips": chips,
        "counts": {"h2": h2_count, "h3": h3_count, "images": len(imgs),
                   "links": len(links), "chars": len(h)},
    }


def print_report(report: dict, out=sys.stdout) -> None:
    for msg in report["passed"]:
        print("  PASS  " + msg, file=out)
    for msg in report["warned"]:
        print("  WARN  " + msg, file=out)
    for msg in report["failed"]:
        print("  FAIL  " + msg, file=out)
    print(file=out)
    print(f"RESULT: {len(report['passed'])} passed, {len(report['failed'])} failed", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check one HTML body against the Canvas-safe rules.")
    ap.add_argument("html_file")
    ap.add_argument("--palette", default=None, help="file of allowed #hex colours; default brand.json")
    ap.add_argument("--no-palette", action="store_true", help="skip the palette check")
    ap.add_argument("--max-alt-len", type=int, default=110)
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    palette = None
    if not args.no_palette:
        palette = load_palette_file(args.palette) if args.palette else brand_palette()
    html = Path(args.html_file).read_text(encoding="utf-8")
    report = check(html, palette=palette, max_alt_len=args.max_alt_len)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
