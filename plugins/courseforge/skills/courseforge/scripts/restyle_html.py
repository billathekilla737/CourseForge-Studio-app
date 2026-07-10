r"""
restyle_html.py (courseforge) - step 2 of the existing-course remediation pipeline
(Dump-CanvasContent.ps1 -> THIS -> Push-CanvasRemediation.ps1).

Deterministic restyler: it only ever touches style="" attributes, injects
wrapper/hero markup around unstructured bodies, and entity-encodes non-ASCII.
It PHYSICALLY CANNOT rewrite instructional prose - and `verify` proves it,
byte-comparing the visible text of every output against its original.

Three looks (--look):
  hybrid (DEFAULT) : filled navy hero + footer only; cards/callouts stay border-only.
                     ~2 Ally "use of color" advisories per page - the deployment default.
  rich             : every component filled (hero, footer, cards, goal, alert, callout).
  clean            : NO background fills anywhere; navy lives in headings + borders.
                     0 use-of-color advisories (for max-score mandates).

Transforms per item (recorded in the manifest as transform_note):
  styled        - templated body: fills/colors adjusted per look, text untouched
  wrapped       - unstructured body (plain prose / iframe embed): hero (real <h2>
                  title) + content card added AROUND the original markup, which is
                  preserved verbatim inside; fixes Ally "missing heading"
  skipped-empty - body has no visible text and no embed; left alone, flagged

Commands:
  python restyle_html.py transform <workdir> [--look hybrid|rich|clean]
  python restyle_html.py verify    <workdir>
  python restyle_html.py scan      <workdir>          # a11y issue report only

<workdir> is the folder Dump-CanvasContent.ps1 wrote (manifest.json + bodies\).
Outputs: styled\<Kind>_<id>.html (pure-ASCII entities - avoids the Canvas
raw-emoji 500), updated manifest.json, verify-report.json. verify exit code =
number of failing items (0 = safe to push).
"""
import argparse, html, json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NAVY = "#061E3F"; GOLD = "#E9A821"
FILLS = {"CARD": "#ffffff", "GOAL": "#eef4fa", "ALERT": "#fbe9eb", "CALLOUT": "#F5F5F5"}

TAG = re.compile(r"<(/?)(\w+)([^>]*?)>", re.I)


# ---------------- shared helpers ----------------

def visible_text(h):
    h = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", h or "", flags=re.I | re.S)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def attr_set(h, attr):
    return sorted(re.findall(attr + r'\s*=\s*"([^"]*)"', h or "", re.I))


def heading_texts(h):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m))).strip()
            for m in re.findall(r"<h[23]\b[^>]*>(.*?)</h[23]>", h or "", re.I | re.S)]


def asciify(h):
    return "".join(c if ord(c) < 128 else "&#%d;" % ord(c) for c in h)


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def strip_fills(h):
    """CLEAN look: remove every background fill; re-ink white/on-navy text."""
    n = len(re.findall(r"background\s*:", h, re.I))
    h = re.sub(r"background\s*:\s*[^;\"']+;?", "", h, flags=re.I)
    h = re.sub(r"color:\s*#ffffff", "color:" + NAVY, h, flags=re.I)
    h = re.sub(r"color:\s*#cfdcec", "color:#4b5563", h, flags=re.I)
    return h, n


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
            rest = recolor_first(rest, r'(<h2\b[^>]*style="[^"]*")', "#ffffff")
            rest = recolor_first(rest, r'</h2>\s*(<p\b[^>]*style="[^"]*")', "#cfdcec")
        elif c == "FOOTER":
            open_tag = set_color(add_prop(open_tag, "background:" + NAVY), "#ffffff")
            rest = re.sub(r"color:\s*#(061e3f|2c3a4d|4b5563|1565c0|236192)", "color:#cfdcec", rest, flags=re.I)
        else:
            open_tag = add_prop(open_tag, "background:" + FILLS[c])
        added += 1
        edits.append((s0, e, open_tag + rest))
    for (s0, e, new) in sorted(edits, key=lambda x: -x[0]):
        h = h[:s0] + new + h[e:]
    return h, added


# ---------------- wrap (unstructured bodies) ----------------

WRAP_OPEN = ('<div style="max-width: 980px; margin: 0 auto; font-family: Inter, '
             "'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55; "
             'color: #2c3a4d;">')
HERO_FILLED = ('<div style="padding: 24px; border-radius: 8px; background: ' + NAVY +
               "; border-top: 5px solid " + GOLD + ';">'
               '<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
               'color: ' + GOLD + '; font-weight: 700;">{eyebrow}</div>'
               '<h2 style="margin: 6px 0 0; font-size: 30px; font-family: Georgia, '
               "'Times New Roman', serif; color: #ffffff;\">{title}</h2></div>")
HERO_CLEAN = ('<div style="padding: 22px 24px; border-radius: 8px; border-top: 5px solid ' + GOLD +
              '; border-left: 8px solid ' + NAVY + ';">'
              '<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; '
              'color: ' + NAVY + '; font-weight: 700;">{eyebrow}</div>'
              '<h2 style="margin: 6px 0 0; font-size: 26px; font-family: Georgia, '
              "'Times New Roman', serif; color: " + NAVY + ';">{title}</h2></div>')
CARD_OPEN = ('<div style="margin-top: 18px; padding: 18px; border-radius: 8px; background: #ffffff; '
             'border: 1px solid #d7dce3; border-top: 4px solid ' + GOLD + '; font-size: 14px; '
             'color: #2c3a4d;">')


def wrap_body(h, title, eyebrow, look):
    hero = (HERO_CLEAN if look == "clean" else HERO_FILLED).format(
        eyebrow=esc(eyebrow), title=esc(title))
    card_open = CARD_OPEN if look != "clean" else CARD_OPEN.replace("background: #ffffff; ", "")
    return WRAP_OPEN + hero + card_open + h + "</div></div>"


# ---------------- manifest plumbing ----------------

def load_manifest(workdir):
    # utf-8-sig: tolerate a BOM from PowerShell 5.1's Set-Content -Encoding UTF8
    with open(os.path.join(workdir, "manifest.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def save_manifest(workdir, m):
    with open(os.path.join(workdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)


def read_body(item):
    with open(item["file"], encoding="utf-8") as f:
        return f.read()


# ---------------- commands ----------------

def cmd_scan(workdir):
    m = load_manifest(workdir)
    total = 0
    for it in m["items"]:
        issues = a11y_issues(read_body(it))
        total += len(issues)
        flag = "CLEAN" if not issues else "; ".join(issues)
        print("  %-11s %-42s %s" % (it["kind"], (it["name"] or "")[:42], flag))
    print("\n%d hard a11y issue(s) across %d items" % (total, len(m["items"])))
    return 0


def cmd_transform(workdir, look):
    m = load_manifest(workdir)
    styled_dir = os.path.join(workdir, "styled")
    os.makedirs(styled_dir, exist_ok=True)
    label = m.get("course_label") or ("Course %s" % m.get("course_id"))
    n_styled = n_wrapped = n_skipped = fills_total = 0
    for it in m["items"]:
        body = read_body(it)
        text = visible_text(body)
        templated = "max-width: 980px" in body.lower() or any(
            k in body.lower() for k in ("border-top: 5px solid #e9a821",
                                        "border-top: 4px solid #e9a821"))
        if not text and "<iframe" not in body.lower():
            it["transform_note"] = "skipped-empty"
            it.pop("styled_file", None)
            n_skipped += 1
            continue
        if templated:
            new, added = transform_components(body, look)
            it["transform_note"] = "styled"
            n_styled += 1
        else:
            new = wrap_body(body, it["name"], label, look)
            added = 0 if look == "clean" else 1
            it["transform_note"] = "wrapped"
            n_wrapped += 1
        it["fills_added"] = added
        fills_total += added
        out = os.path.join(styled_dir, os.path.basename(it["file"]))
        with open(out, "w", encoding="utf-8") as f:
            f.write(asciify(new))
        it["styled_file"] = out
    m["look"] = look
    save_manifest(workdir, m)
    print("TRANSFORM (--look %s): %d styled, %d wrapped, %d skipped-empty; %d fill(s) added"
          % (look, n_styled, n_wrapped, n_skipped, fills_total))
    print("Ally 'use of color' advisories expected from fills: ~%d course-wide" % fills_total)
    return 0


def cmd_verify(workdir):
    m = load_manifest(workdir)
    look = m.get("look", "hybrid")
    report, fails = [], 0
    for it in m["items"]:
        if "styled_file" not in it:
            continue
        orig = read_body(it)
        with open(it["styled_file"], encoding="utf-8") as f:
            new = f.read()
        issues = []
        vo, vn = visible_text(orig), visible_text(new)
        if it["transform_note"] == "styled":
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
        if look == "clean" and re.search(r"background\s*:", new, re.I):
            issues.append("clean look still has a fill")
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
        ok = not issues
        if not ok:
            fails += 1
        report.append({"kind": it["kind"], "id": it["id"], "name": it["name"],
                       "ok": ok, "issues": issues, "a11y_after": after})
        print("  [%s] %-11s %-40s %s" % ("PASS" if ok else "FAIL", it["kind"],
                                         (it["name"] or "")[:40],
                                         "" if ok else "; ".join(issues)))
    with open(os.path.join(workdir, "verify-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("\nVERIFY: %d item(s), %d failed -> %s" %
          (len(report), fails, "SAFE TO PUSH" if fails == 0 else "DO NOT PUSH FAILURES"))
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "transform", "verify"])
    ap.add_argument("workdir")
    ap.add_argument("--look", default="hybrid", choices=["hybrid", "rich", "clean"])
    a = ap.parse_args()
    if a.cmd == "scan":
        return cmd_scan(a.workdir)
    if a.cmd == "transform":
        return cmd_transform(a.workdir, a.look)
    return cmd_verify(a.workdir)


if __name__ == "__main__":
    sys.exit(main())
