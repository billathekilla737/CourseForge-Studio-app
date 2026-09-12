r"""
restyle.py (courseforge.a11y) - step 2 of the existing-course remediation
pipeline (dump -> THIS -> push).

Deterministic restyler: it only ever touches style="" attributes, injects
wrapper/hero markup around unstructured bodies, and entity-encodes non-ASCII.
It PHYSICALLY CANNOT rewrite instructional prose - and `verify` proves it,
byte-comparing the visible text of every output against its original.

Three looks:
  clean (DEFAULT)  : NO background fills anywhere; navy lives in headings + borders.
                     0 use-of-color advisories (for max-score mandates).
  hybrid           : filled navy hero + footer only; cards/callouts stay border-only.
                     ~2 Ally "use of color" advisories per page.
  rich             : every component filled (hero, footer, cards, goal, alert, callout).

Transforms per item (recorded in the manifest as transform_note):
  styled        - templated body: fills/colors adjusted per look, text untouched
  wrapped       - unstructured body (plain prose / iframe embed): hero (real <h2>
                  title) + content card added AROUND the original markup, which is
                  preserved verbatim inside; fixes Ally "missing heading"
  skipped-empty - body has no visible text and no embed; left alone, flagged

Library surface (what the routes, the CLI and the batch runner call):
  configure(brand_path)          palette from brand.json (cfg.brand_path / CF_BRAND)
  transform(workdir, look)       -> summary dict; writes styled/<Kind>_<id>.html
  verify(workdir)                -> report dict {"items", "fails", "ok", "look"};
                                    writes verify-report.json (a list of records,
                                    each carrying the sha256 of the bytes verified)
  scan(workdir)                  -> [{key, kind, id, name, issues}]
  a11y_issues(html), visible_text(html), attr_set(html, attr), asciify(html)

The command line (`main`) is kept for the Assistant and for hand runs:
  python -m courseforge.a11y.restyle transform <workdir> [--look hybrid|rich|clean]
  python -m courseforge.a11y.restyle verify    <workdir>
  python -m courseforge.a11y.restyle scan      <workdir>

<workdir> holds manifest.json + bodies/. Paths in the manifest may be absolute
or relative to the workdir. Outputs: styled/<Kind>_<id>.html (pure-ASCII
entities - avoids the Canvas raw-emoji 500), updated manifest.json,
verify-report.json. verify exit code = number of failing items (0 = safe to push).
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

LOOKS = ("clean", "hybrid", "rich")


def _default_brand_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "brand.json")


def load_brand(path=None):
    """Palette and fonts for the markup we WRITE, from brand.json (override
    with `path`, else CF_BRAND). Falls back to the MGCCC defaults so a missing
    or damaged config never stops a remediation run."""
    default = {
        "colors": {"navy": "#061E3F", "gold": "#E9A821", "blue": "#236192",
                   "red": "#C11F31", "body_text": "#2c3a4d",
                   "muted_text": "#4b5563", "on_navy_text": "#ffffff",
                   "on_navy_muted": "#cfdcec", "hairline": "#d7dce3",
                   "page_bg": "#f5f6f8", "card_fill": "#ffffff",
                   "goal_fill": "#eef4fa", "alert_fill": "#fbe9eb",
                   "callout_fill": "#F5F5F5"},
        "fonts": {"display": "Georgia, 'Times New Roman', serif",
                  "body": "Inter, 'Segoe UI', Roboto, Helvetica, Arial, "
                          "sans-serif",
                  "mono": "Consolas, 'Courier New', monospace"}}
    path = path or os.environ.get("CF_BRAND") or _default_brand_path()
    try:
        with open(path, encoding="utf-8-sig") as f:
            loaded = json.load(f)
        # brand values land inside style="" attributes: a quote or a URL in
        # one would break out of the attribute, so only plain colours and
        # font lists are accepted
        ok = {"colors": re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}|rgba?\([\d\s,.%]+\))$"),
              "fonts": re.compile(r"^[\w\s,'\-]+$")}
        for section in ("colors", "fonts"):
            for k, v in (loaded.get(section) or {}).items():
                if isinstance(v, str) and v.strip() and ok[section].match(v.strip()):
                    default[section][k] = v.strip()
                elif isinstance(v, str):
                    print("WARN: ignoring brand %s.%s=%r (not a plain value)" % (section, k, v[:40]))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("WARN: could not read %s (%s) - using the built-in palette"
              % (path, e))
    default["path"] = path
    return default


# Module constants, (re)built from the brand by _apply_brand below so a
# different brand.json (cfg.brand_path) changes what is emitted.
BRAND = None
C = None
F = None
NAVY = GOLD = None
FILLS = None
WRAP_OPEN = HERO_FILLED = HERO_CLEAN = CARD_OPEN = None


def _apply_brand(brand):
    global BRAND, C, F, NAVY, GOLD, FILLS, WRAP_OPEN, HERO_FILLED, HERO_CLEAN, CARD_OPEN
    BRAND = brand
    C = brand["colors"]
    F = brand["fonts"]
    NAVY = C["navy"]
    GOLD = C["gold"]
    FILLS = {"CARD": C["card_fill"], "GOAL": C["goal_fill"],
             "ALERT": C["alert_fill"], "CALLOUT": C["callout_fill"]}
    WRAP_OPEN = ('<div style="max-width: 980px; margin: 0 auto; font-family: '
                 + F["body"] + '; line-height: 1.55; color: '
                 + C["body_text"] + ';">')
    HERO_FILLED = ('<div style="padding: 24px; border-radius: 8px; background: ' + NAVY +
                   "; border-top: 5px solid " + GOLD + ';">'
                   '<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
                   'color: ' + GOLD + '; font-weight: 700;">{eyebrow}</div>'
                   '<h2 style="margin: 6px 0 0; font-size: 30px; font-family: '
                   + F["display"] + '; color: ' + C["on_navy_text"]
                   + ';">{title}</h2></div>')
    HERO_CLEAN = ('<div style="padding: 22px 24px; border-radius: 8px; border-top: 5px solid ' + GOLD +
                  '; border-left: 8px solid ' + NAVY + ';">'
                  '<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
                  'font-weight: 700;">{eyebrow}</div>'
                  '<h2 style="margin: 6px 0 0; font-size: 26px; font-family: '
                  + F["display"] + '; color: ' + NAVY + ';">{title}</h2></div>')
    CARD_OPEN = ('<div style="margin-top: 18px; padding: 18px; border-radius: 8px; '
                 'background: ' + C["card_fill"] + '; border: 1px solid '
                 + C["hairline"] + '; border-top: 4px solid ' + GOLD
                 + '; font-size: 14px; color: ' + C["body_text"] + ';">')


def configure(brand_path=None):
    """Point the restyler at a brand file. Empty means courseforge/brand.json
    (or CF_BRAND). Returns the palette in use."""
    _apply_brand(load_brand(brand_path or None))
    return BRAND


_apply_brand(load_brand())

# DETECTOR_NOTE: the hex literals inside classify() and strip_fills() below are
# NOT style choices and are deliberately not read from brand.json. They match
# the border colours of the EXISTING MGCCC page template in order to recognise
# a hero / card / callout inside a body somebody already built - a fingerprint
# of the markup being read, not of the markup being written. Another
# institution restyling its own existing template needs new detector patterns
# here, not just new colours in brand.json.

TAG = re.compile(r"<(/?)(\w+)([^>]*?)>", re.I)


# ---------------- shared helpers ----------------

def visible_text(h):
    h = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", h or "", flags=re.I | re.S)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


# Tags that do not separate words on screen. `visible_text` turns every tag into
# a space, which is right for block tags and wrong for these: a browser renders
# `<span>Friday</span>.` as "Friday." while visible_text reads "Friday ." So a
# gate built on visible_text refuses any edit that unwraps an inline tag next to
# punctuation, which is exactly what removing a bordered box or an unwanted bold
# run does. `br` and `wbr` stay out of the set: they really do break the line.
INLINE_TAGS = ("a|abbr|acronym|b|bdi|bdo|big|cite|code|data|del|dfn|em|font|i|ins|kbd|"
               "mark|q|rp|rt|ruby|s|samp|small|span|strike|strong|sub|sup|time|tt|u|var")
_INLINE = re.compile(r"</?(?:%s)\b[^>]*>" % INLINE_TAGS, re.I)


def reader_text(h):
    """The words a reader sees, with inline tags closed up rather than spaced.

    Use this, not `visible_text`, when the gate has to hold across an edit that
    deletes inline markup. It still catches a changed, reordered, dropped or
    merged word; it only stops counting the space a `</span>` used to leave.
    """
    h = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", h or "", flags=re.I | re.S)
    h = _INLINE.sub("", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def same_reader_text(a, b) -> bool:
    return reader_text(a) == reader_text(b)


def attr_set(h, attr):
    return sorted(re.findall(attr + r'\s*=\s*"([^"]*)"', h or "", re.I))


def heading_texts(h):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m))).strip()
            for m in re.findall(r"<h[23]\b[^>]*>(.*?)</h[23]>", h or "", re.I | re.S)]


def asciify(h):
    return "".join(c if ord(c) < 128 else "&#%d;" % ord(c) for c in h)


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- a11y scan (the hard Ally rules) ----------------

IMG = re.compile(r"<img\b[^>]*>", re.I)
ALT = re.compile(r'alt\s*=\s*"(.*?)"', re.I | re.S)
FNAME = re.compile(r"\.(png|jpe?g|gif|svg|webp|bmp|tiff?)\s*$", re.I)
TABLE = re.compile(r"<table\b.*?</table>", re.I | re.S)
LINK = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)


def a11y_issues(h):
    issues = []
    for tag in IMG.findall(h or ""):
        m = ALT.search(tag)
        if not m:
            issues.append("img missing alt")
        else:
            alt = m.group(1).strip()
            if alt == "":
                pass  # decorative
            elif FNAME.search(alt) or (" " not in alt and "." in alt):
                issues.append("alt is a filename: %r" % alt[:40])
            elif len(alt) > 110:
                issues.append("alt too long (%d chars)" % len(alt))
    text = visible_text(h)
    has_h2 = bool(re.search(r"<h2\b", h or "", re.I))
    has_h3 = bool(re.search(r"<h3\b", h or "", re.I))
    if len(text) > 350 and not has_h2 and not has_h3:
        issues.append("no semantic heading (h2/h3)")
    if re.search(r"<h[45]\b", h or "", re.I) and not has_h3:
        issues.append("skipped heading level")
    for tbl in TABLE.findall(h or ""):
        if not re.search(r"<th\b[^>]*scope", tbl, re.I):
            issues.append("table without <th scope>")
    for inner in LINK.findall(h or ""):
        if re.sub(r"<[^>]+>", "", inner).strip() == "" and not re.search(r"<img", inner, re.I):
            issues.append("empty link (no text)")
    # active content in course HTML: Canvas strips it on save, but it should
    # never be carried through a remediation pass silently
    if re.search(r"<script\b", h or "", re.I):
        issues.append("script element in page HTML")
    if re.search(r"\son[a-z]+\s*=", h or "", re.I):
        issues.append("inline event handler attribute")
    if re.search(r"(href|src)\s*=\s*[\"']\s*javascript:", h or "", re.I):
        issues.append("javascript: link")
    return issues


# ---------------- component transform (templated bodies) ----------------

def find_div_spans(h):
    """(start, end, style) for every <div>, nesting-aware."""
    spans, stack = [], []
    for m in TAG.finditer(h):
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag != "div":
            continue
        if not closing:
            st = re.search(r'style\s*=\s*"([^"]*)"', attrs, re.I)
            stack.append((m.start(), st.group(1) if st else ""))
        elif stack:
            s0, style = stack.pop()
            spans.append((s0, m.end(), style))
    return spans


def add_prop(open_tag, prop):
    return re.sub(r'(style\s*=\s*"[^"]*?)"', r"\1;" + prop + '"', open_tag, count=1, flags=re.I)


def set_color(open_tag, color):
    def repl(m):
        style = re.sub(r"color\s*:\s*#[0-9a-fA-F]{3,6}\s*;?", "", m.group(1), flags=re.I)
        return 'style="' + style.rstrip().rstrip(";") + ";color:" + color + '"'
    return re.sub(r'style\s*=\s*"([^"]*)"', repl, open_tag, count=1, flags=re.I)


def recolor_first(rest, pattern, color):
    m = re.search(pattern, rest, re.I)
    if not m:
        return rest
    g = 1 if m.groups() else 0
    return rest[:m.start(g)] + set_color(m.group(g), color) + rest[m.end(g):]


def classify(style, has_h2):
    s = style.lower()
    if "background:" in s:
        return None  # already filled - idempotent skip
    gold5 = "border-top: 5px solid #e9a821" in s
    if gold5 and has_h2:
        return "HERO"
    if gold5 and not has_h2:
        return "FOOTER"
    if ("border-top: 4px solid #e9a821" in s) or ("border-width: 4px 1px 1px" in s):
        return "CARD"
    if "border-left: 4px solid #236192" in s:
        return "GOAL"
    if ("border-left: 5px solid #c11f31" in s) or ("#c11f31" in s and "border-color" in s):
        return "ALERT"
    if ("border-left: 4px solid #e9a821" in s) or ("border: 1px solid #e9a821" in s):
        return "CALLOUT"
    return None


COLOR_DECL = re.compile(r"(?<![-a-zA-Z])color\s*:\s*[^;\"']+;?", re.I)
OPEN_TAG = re.compile(r"<(\w+)\b([^>]*?)>", re.S)


def strip_fills(h):
    """CLEAN look, per the field-proven spec in ada-remediation.md:
      - NO background fills anywhere (Ally flags every fill, even white)
      - color lives ONLY in navy <h2>/<h3> headings and borders
      - ALL other text drops its color declaration -> default black
        (chromatic non-heading text is itself a 'use of color' flag, and
        orphaned gold-on-white is a hard CONTRAST failure)
      - links (<a>) keep their color (house style pairs it with underline)
    Borders are never touched (headings + borders are Ally-exempt)."""
    n = len(re.findall(r"background(?:-color)?\s*:", h, re.I))
    h = re.sub(r"background(?:-color)?\s*:\s*[^;\"']+;?", "", h, flags=re.I)

    def per_tag(m):
        tag, attrs = m.group(1).lower(), m.group(2)
        if "style" not in attrs.lower():
            return m.group(0)

        def per_style(sm):
            s = sm.group(1)
            if tag in ("h2", "h3"):
                s = COLOR_DECL.sub("", s).rstrip().rstrip(";") + ";color:" + NAVY
            elif tag != "a":
                s = COLOR_DECL.sub("", s)
            return 'style="%s"' % s.strip().strip(";")

        attrs2 = re.sub(r'style\s*=\s*"([^"]*)"', per_style, attrs, count=1, flags=re.I)
        return "<%s%s>" % (m.group(1), attrs2)

    return OPEN_TAG.sub(per_tag, h), n


def transform_components(h, look):
    """Inject fills per look into a templated body. Only style attrs change."""
    if look == "clean":
        return strip_fills(h)
    wanted = {"hybrid": {"HERO", "FOOTER"},
              "rich": {"HERO", "FOOTER", "CARD", "GOAL", "ALERT", "CALLOUT"}}[look]
    spans = find_div_spans(h)
    edits, taken, added = [], [], 0
    for (s0, e, style) in sorted(spans, key=lambda x: (x[0], -x[1])):
        slice_ = h[s0:e]
        c = classify(style, bool(re.search(r"<h2\b", slice_, re.I)))
        if not c or c not in wanted:
            continue
        if any(a <= s0 and e <= b for (a, b) in taken):
            continue
        taken.append((s0, e))
        open_end = h.index(">", s0) + 1
        open_tag, rest = h[s0:open_end], h[open_end:e]
        if c == "HERO":
            open_tag = add_prop(open_tag, "background:" + NAVY)
            rest = recolor_first(rest, r'(<div\b[^>]*style="[^"]*")', GOLD)
            rest = recolor_first(rest, r'(<h2\b[^>]*style="[^"]*")',
                                 C["on_navy_text"])
            rest = recolor_first(rest,
                                 r'</h2>\s*(<p\b[^>]*style="[^"]*")',
                                 C["on_navy_muted"])
        elif c == "FOOTER":
            open_tag = set_color(add_prop(open_tag, "background:" + NAVY),
                                 C["on_navy_text"])
            # DETECTOR_NOTE applies: this matches the template's own
            # dark text colours so they can be lifted off a navy fill.
            rest = re.sub(r"color:\s*#(061e3f|2c3a4d|4b5563|1565c0|236192)",
                          "color:" + C["on_navy_muted"], rest, flags=re.I)
        else:
            open_tag = add_prop(open_tag, "background:" + FILLS[c])
        added += 1
        edits.append((s0, e, open_tag + rest))
    for (s0, e, new) in sorted(edits, key=lambda x: -x[0]):
        h = h[:s0] + new + h[e:]
    return h, added


# ---------------- wrap (unstructured bodies) ----------------

def wrap_body(h, title, eyebrow, look):
    """Hero + content card around an unstructured body.

    The CLEAN look must emit no background fill AND no colour on anything but
    h2/h3/a - that is exactly what verify enforces. The wrapper used to
    carry `color: <body_text>` on its outer div and card under every look, so
    clean-look output FAILED ITS OWN VERIFY and could never be pushed (only
    wrapped bodies were affected, which is why templated courses never hit
    it). Clean now drops both the fill and the colour and inherits Canvas's
    default text colour, which is what "no use of colour" means anyway."""
    if look == "clean":
        hero = HERO_CLEAN.format(eyebrow=esc(eyebrow), title=esc(title))
        wrap_open = WRAP_OPEN.replace("; color: " + C["body_text"], "")
        card_open = (CARD_OPEN
                     .replace("background: " + C["card_fill"] + "; ", "")
                     .replace("; color: " + C["body_text"], ""))
    else:
        hero = HERO_FILLED.format(eyebrow=esc(eyebrow), title=esc(title))
        wrap_open, card_open = WRAP_OPEN, CARD_OPEN
    return wrap_open + hero + card_open + h + "</div></div>"


# ---------------- manifest plumbing ----------------

def resolve_path(workdir, p):
    """Manifest paths may be absolute (the old PowerShell dump) or relative to
    the workdir (the Studio dump, so a data folder can move between machines)."""
    p = str(p)
    return p if os.path.isabs(p) else os.path.join(str(workdir), p)


def load_manifest(workdir):
    # utf-8-sig: tolerate a BOM from PowerShell 5.1's Set-Content -Encoding UTF8
    with open(os.path.join(str(workdir), "manifest.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def save_manifest(workdir, m):
    with open(os.path.join(str(workdir), "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)


def read_body(item, workdir=""):
    with open(resolve_path(workdir, item["file"]), encoding="utf-8") as f:
        return f.read()


def read_styled(item, workdir=""):
    with open(resolve_path(workdir, item["styled_file"]), encoding="utf-8") as f:
        return f.read()


def item_key(it):
    return it.get("key") or "%s_%s" % (str(it.get("kind", "")).lower(), it.get("id"))


# ---------------- commands ----------------

def scan(workdir):
    """Hard a11y issues per item, from the dumped bodies. No files change."""
    m = load_manifest(workdir)
    out = []
    for it in m["items"]:
        out.append({"key": item_key(it), "kind": it["kind"], "id": it["id"],
                    "name": it.get("name"), "issues": a11y_issues(read_body(it, workdir))})
    return out


def cmd_scan(workdir):
    rows = scan(workdir)
    total = 0
    for r in rows:
        total += len(r["issues"])
        flag = "CLEAN" if not r["issues"] else "; ".join(r["issues"])
        print("  %-11s %-42s %s" % (r["kind"], (r["name"] or "")[:42], flag))
    print("\n%d hard a11y issue(s) across %d items" % (total, len(rows)))
    return 0


def transform(workdir, look="clean", log=None):
    """Write styled/<file> for every non-empty body. Returns a summary dict.
    Only style attributes change; visible text is untouched (verify proves it)."""
    if look not in LOOKS:
        raise ValueError("unknown look %r (choose clean, hybrid or rich)" % (look,))
    m = load_manifest(workdir)
    styled_dir = os.path.join(str(workdir), "styled")
    os.makedirs(styled_dir, exist_ok=True)
    label = m.get("course_label") or ("Course %s" % m.get("course_id"))
    n_styled = n_wrapped = n_skipped = fills_total = 0
    for it in m["items"]:
        body = read_body(it, workdir)
        text = visible_text(body)
        templated = "max-width: 980px" in body.lower() or any(
            k in body.lower() for k in ("border-top: 5px solid #e9a821",
                                        "border-top: 4px solid #e9a821"))
        if not text and "<iframe" not in body.lower():
            it["transform_note"] = "skipped-empty"
            it.pop("styled_file", None)
            it.pop("fills_added", None)
            n_skipped += 1
            continue
        if templated:
            new, added = transform_components(body, look)
            it["transform_note"] = "styled"
            n_styled += 1
        else:
            new = wrap_body(body, it.get("name") or it.get("title") or "", label, look)
            added = 0 if look == "clean" else 1
            it["transform_note"] = "wrapped"
            n_wrapped += 1
        it["fills_added"] = added
        fills_total += added
        out_name = os.path.basename(str(it["file"]))
        out_abs = os.path.join(styled_dir, out_name)
        with open(out_abs, "w", encoding="utf-8", newline="") as f:
            f.write(asciify(new))
        it["styled_file"] = (out_abs if os.path.isabs(str(it["file"]))
                             else os.path.join("styled", out_name))
        if log:
            log("%s: %s" % (it["transform_note"], it.get("name") or out_name))
    m["look"] = look
    m["transformed_at"] = _now()
    m.pop("verified_at", None)
    m.pop("verify_fails", None)
    save_manifest(workdir, m)
    return {"look": look, "items": len(m["items"]), "styled": n_styled,
            "wrapped": n_wrapped, "skipped": n_skipped, "fills": fills_total}


def cmd_transform(workdir, look):
    s = transform(workdir, look)
    if look == "clean":
        # in clean mode `fills` is the count of fills REMOVED (strip_fills)
        print("TRANSFORM (--look clean): %d styled, %d wrapped, %d skipped-empty; %d fill(s) removed"
              % (s["styled"], s["wrapped"], s["skipped"], s["fills"]))
        print("Ally 'use of color' advisories expected: ~0 (no background fills remain)")
    else:
        print("TRANSFORM (--look %s): %d styled, %d wrapped, %d skipped-empty; %d fill(s) added"
              % (look, s["styled"], s["wrapped"], s["skipped"], s["fills"]))
        print("Ally 'use of color' advisories expected from fills: ~%d course-wide "
              "(reviewable/mark-resolved; use --look clean for ~0)" % s["fills"])
    return 0


def verify_item(orig, new, transform_note, look):
    """The checks for one item. Returns (issues, a11y_issues_after)."""
    issues = []
    vo, vn = visible_text(orig), visible_text(new)
    if transform_note == "styled":
        if vo != vn:
            issues.append("VISIBLE TEXT CHANGED")
        if heading_texts(orig) != heading_texts(new):
            issues.append("headings changed")
    else:  # wrapped: original text must survive verbatim inside the new body
        if vo and vo not in vn:
            issues.append("ORIGINAL TEXT NOT PRESERVED inside wrapper")
        if not re.search(r"<h2\b", new, re.I):
            issues.append("wrapper missing <h2>")
    if attr_set(orig, "href") != attr_set(new, "href"):
        issues.append("href set changed")
    if attr_set(orig, "src") != attr_set(new, "src"):
        issues.append("src set changed")
    if any(ord(c) >= 128 for c in new):
        issues.append("non-ASCII survived (emoji-500 risk)")
    if look == "clean":
        if re.search(r"background\s*:", new, re.I):
            issues.append("clean look still has a fill")
        # full clean spec: NO color declaration outside h2/h3/a. A stray
        # chromatic non-heading color is a use-of-color flag, and orphaned
        # light text (gold/white) on the now-white page is a contrast FAIL.
        for mm in OPEN_TAG.finditer(new):
            tg = mm.group(1).lower()
            if tg in ("h2", "h3", "a"):
                continue
            st = re.search(r'style\s*=\s*"([^"]*)"', mm.group(2), re.I)
            if st and COLOR_DECL.search(st.group(1)):
                issues.append("clean look: colored non-heading <%s> text survived" % tg)
                break
    # dark-on-navy: inside every navy-FILLED div (exact span, nesting-aware),
    # no dark text colors may remain
    for (s0, e, style) in find_div_spans(new):
        if not re.search(r"background:\s*#061e3f", style, re.I):
            continue
        if re.search(r'color:\s*#(061e3f|2c3a4d|4b5563)', new[s0:e], re.I):
            issues.append("dark-on-navy text")
            break
    after = a11y_issues(new)
    heading_fixed = ("no semantic heading" not in " ".join(a11y_issues(orig))) or \
                    ("no semantic heading" not in " ".join(after))
    if not heading_fixed:
        issues.append("missing-heading not fixed")
    return issues, after


def verify(workdir, log=None):
    """Prove every styled file against its original. Writes verify-report.json
    (a list, one record per styled item, each with the sha256 of the exact
    bytes verified) and returns {"items", "fails", "ok", "look", "count"}."""
    m = load_manifest(workdir)
    look = m.get("look", "hybrid")
    report, fails = [], 0
    for it in m["items"]:
        if "styled_file" not in it:
            continue
        orig = read_body(it, workdir)
        new = read_styled(it, workdir)
        issues, after = verify_item(orig, new, it.get("transform_note"), look)
        ok = not issues
        if not ok:
            fails += 1
        # sha256 of the EXACT bytes verified. The push gates on this report,
        # but nothing used to tie the report to the files it looked at:
        # verify -> re-run transform -> push passed on a stale pass. The
        # digest makes a stale report detectable instead of trusted.
        report.append({"key": item_key(it), "kind": it["kind"], "id": it["id"],
                       "name": it.get("name"), "ok": ok, "issues": issues,
                       "a11y_after": after, "styled_file": it["styled_file"],
                       "styled_sha256": hashlib.sha256(new.encode("utf-8")).hexdigest()})
        if log:
            log("[%s] %s %s%s" % ("PASS" if ok else "FAIL", it["kind"], it.get("name") or "",
                                  "" if ok else ": " + "; ".join(issues)))
    with open(os.path.join(str(workdir), "verify-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    m["verified_at"] = _now()
    m["verify_fails"] = fails
    save_manifest(workdir, m)
    return {"items": report, "fails": fails, "ok": fails == 0, "look": look,
            "count": len(report)}


def cmd_verify(workdir):
    def out(line):
        print("  " + line)
    r = verify(workdir, log=out)
    print("\nVERIFY: %d item(s), %d failed -> %s" %
          (r["count"], r["fails"], "SAFE TO PUSH" if r["fails"] == 0 else "DO NOT PUSH FAILURES"))
    return r["fails"]


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "transform", "verify"])
    ap.add_argument("workdir")
    # clean is the DEFAULT: zero background fills => ~0 Ally "use of color"
    # advisories, the safe choice for a compliance-driven remediation. hybrid/rich
    # are opt-in for a looks overhaul when the instructor accepts ~2-7 reviewable
    # flags per page.
    ap.add_argument("--look", default="clean", choices=list(LOOKS))
    ap.add_argument("--brand", default=None, help="brand.json to read the palette from")
    a = ap.parse_args(argv)
    if a.brand:
        configure(a.brand)
    if a.cmd == "scan":
        return cmd_scan(a.workdir)
    if a.cmd == "transform":
        return cmd_transform(a.workdir, a.look)
    return cmd_verify(a.workdir)


if __name__ == "__main__":
    sys.exit(main())
