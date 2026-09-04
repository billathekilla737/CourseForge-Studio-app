"""slo_framework_tool.py (courseforge)

Mississippi Curriculum Framework -> Student Learning Outcomes, for any program.

The MCCB publishes one framework PDF per program; each defines, per course, a nested list of
Student Learning Outcomes. This tool resolves a Canvas course to its program, extracts that
course's SLOs, and turns an agent-written alignment map into an instructor-facing report.

Subcommands
  index     fetch/cache the program index (151+ programs: name, CIP, prefixes, page slug)
  resolve   Canvas course_code -> candidate programs (by prefix, CIP, or name)
  fetch     download a program's current (and prior) framework PDF
  slos      extract courses + nested SLOs from a framework PDF -> slos.json
  validate  check an agent-written alignment.json is complete and honest
  report    render the alignment as markdown + Canvas-safe HTML

Deliberate split of labor: this tool does the mechanical work (scrape, parse, validate,
render). Judging whether an assignment actually satisfies an outcome is left to the agent,
which writes alignment.json between `slos` and `report`. `validate` is what stops the agent
from hand-waving.

Stdlib + PyMuPDF only. ASCII only.
"""
import argparse, html, json, os, re, sys, urllib.parse, urllib.request

INDEX_URL = "https://www.mccb.edu/curriculum"
BASE = "https://www.mccb.edu"
UA = "Mozilla/5.0 (compatible; CourseForge SLO alignment)"

VERDICTS = ("assessed", "partially-assessed", "ungraded-only", "not-assessed", "not-applicable")


# ---------------------------------------------------------------- http helpers

def _get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def _abs(href):
    return href if href.startswith("http") else BASE + href


def _norm_slug(s):
    """Accept every shape a slug arrives in: a full URL, '/curriculum/x', a bare 'x', or the
    MSYS-mangled 'C:/Program Files/Git/curriculum/x' that Git Bash produces from a
    leading-slash argument."""
    s = (s or "").strip().replace("\\", "/")
    if s.startswith("http"):
        return s
    m = re.search(r"/curriculum/[^/\s]+", s)
    if m:
        return m.group(0)
    tail = s.strip("/").split("/")[-1]
    if not tail:
        raise ValueError("empty program slug")
    return "/curriculum/" + tail


# --------------------------------------------------------------- program index

def parse_index(page):
    """The index is a Drupal view of <a class="curriculum-card"> cards carrying the program
    name, CIP classification, CIP code and course prefixes."""
    out = []
    for href, body in re.findall(r'<a class="curriculum-card" href="([^"]+)">(.*?)</a>', page, re.S):
        def span(cls):
            m = re.search(r'<span class="%s">(?:<strong>[^<]*</strong>)?(.*?)</span>' % cls, body, re.S)
            return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
        name = span("cip-card-title")
        if not name or name.upper() == "TBD":
            continue
        out.append({
            "name": name,
            "cip_class": span("cip-card-name"),
            "cip": span("cip-card-code"),
            "prefixes": [p.strip().upper() for p in span("cip-card-prefix").split(",") if p.strip()],
            "slug": href,
            "url": _abs(href),
        })
    return out


def cmd_index(a):
    if a.cache and os.path.exists(a.cache) and not a.refresh:
        progs = json.load(open(a.cache, encoding="utf-8-sig"))["programs"]
        src = "cache"
    else:
        progs = parse_index(_get(INDEX_URL))
        src = "live"
        if a.cache:
            os.makedirs(os.path.dirname(os.path.abspath(a.cache)) or ".", exist_ok=True)
            json.dump({"index_url": INDEX_URL, "count": len(progs), "programs": progs},
                      open(a.cache, "w", encoding="utf-8"), indent=1)
    if not progs:
        print("REFUSING: parsed 0 programs. The index markup likely changed; "
              "re-check %s before trusting any alignment result." % INDEX_URL)
        return 2
    pref = {}
    for p in progs:
        for x in p["prefixes"]:
            pref.setdefault(x, []).append(p["name"])
    print("%d programs (%s), %d distinct course prefixes" % (len(progs), src, len(pref)))
    if a.list_prefixes:
        for k in sorted(pref):
            print("  %-6s %d" % (k, len(pref[k])))
    return 0


def load_index(cache):
    if cache and os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8-sig"))["programs"]
    return parse_index(_get(INDEX_URL))


# -------------------------------------------------------------------- resolve

def cmd_resolve(a):
    progs = load_index(a.cache)
    prefix = num = None
    if a.course_code:
        m = re.match(r"\s*([A-Za-z]{2,4})\s*(\d{3,4})?", a.course_code)
        if m:
            prefix, num = m.group(1).upper(), m.group(2)

    hits = progs
    if a.cip:
        hits = [p for p in hits if p["cip"] == a.cip]
    if a.name:
        hits = [p for p in hits if a.name.lower() in p["name"].lower()]
    if prefix and not a.cip and not a.name:
        hits = [p for p in hits if prefix in p["prefixes"]]

    result = {"query": {"course_code": a.course_code, "prefix": prefix, "number": num,
                        "cip": a.cip, "name": a.name},
              "candidates": hits}

    if not hits:
        result["conclusion"] = (
            "No MCCB CTE framework claims prefix %r. Academic-transfer courses (English, math, "
            "history, most CSC) are governed by the statewide articulation agreement and common "
            "course numbering, NOT by these CTE frameworks. Report that rather than forcing a "
            "match." % prefix)
    elif len(hits) > 1:
        result["conclusion"] = (
            "%d programs share this prefix. Disambiguate by checking which framework's course "
            "list contains the course number (run `slos` on each candidate and match on number "
            "AND title), or ask the instructor which program the course belongs to." % len(hits))
    else:
        result["conclusion"] = "Single match."

    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["conclusion"])
        for p in hits:
            print("  %-58s CIP %-9s prefix %-12s %s"
                  % (p["name"][:58], p["cip"], ",".join(p["prefixes"]), p["url"]))
    return 0


# ---------------------------------------------------------------- framework pdf

def cmd_fetch(a):
    try:
        slug = _norm_slug(a.slug)
        page = _get(_abs(slug))
    except Exception as ex:
        print("REFUSING: could not load the program page for %r (%s: %s)"
              % (a.slug, type(ex).__name__, ex))
        return 2
    a.slug = slug
    links = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', page, re.S | re.I):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
        if "framework" in label.lower():          # excludes unrelated site PDFs
            links.append({"label": label, "url": _abs(html.unescape(href))})
    if not links:
        print("REFUSING: no framework PDF link found on %s" % _abs(a.slug))
        return 2

    os.makedirs(a.out, exist_ok=True)
    got = []
    for L in links:
        current = "past" not in L["label"].lower()
        yr = re.search(r"(20\d{2})", os.path.basename(L["url"]))
        name = ("current" if current else "prior") + "-" + os.path.basename(
            urllib.parse.unquote(L["url"]))[:80]
        path = os.path.join(a.out, name)
        open(path, "wb").write(_get(L["url"], binary=True))
        rec = {"label": L["label"], "url": L["url"], "path": path, "current": current,
               "year": yr.group(1) if yr else None, "bytes": os.path.getsize(path)}
        got.append(rec)
        print("%-8s %-34s %7d bytes  %s" % ("CURRENT" if current else "prior", L["label"],
                                            rec["bytes"], path))
    json.dump({"slug": a.slug, "page": _abs(a.slug), "pdfs": got},
              open(os.path.join(a.out, "framework-sources.json"), "w", encoding="utf-8"), indent=2)
    if not any(g["current"] for g in got):
        print("WARNING: no link was labelled as the current framework; treat year with care.")
    return 0


# ------------------------------------------------------------------ slo parser

# The colon is optional: some frameworks (Welding 2018) omit it on the first block.
COURSE_MARK = re.compile(r"Course Number and Name\s*:?", re.I)
# Two layouts seen: code and title on ONE line (Simulation 2025, IT 2017), or the code alone
# on its line with the title on the next (Welding 2018).
CODE_INLINE = re.compile(r"^\s*([A-Z]{2,4})\s?(\d{3,4})\s+(\S.*?)\s*$")
CODE_ALONE = re.compile(r"^\s*([A-Z]{2,4})\s?(\d{3,4})\s*$")
SLO_MARK = re.compile(r"Student Learning Outcomes?\s*:", re.I)
# section headers that end the SLO list inside a course block
END_MARK = re.compile(r"^\s*(Description|Hour Breakdown|Prerequisite|Corequisite|"
                      r"Suggested Enabling|Competenc\w*|Recommended|Textbook)\s*:", re.I)
# The text may be EMPTY on the marker's own line (Simulation 2025 puts "1." alone, then the
# outcome on the next line); the continuation branch fills it in. Requiring whitespace-or-EOL
# after the dot keeps "e.g." and "1.5" from being read as list markers.
L1 = re.compile(r"^\s*(\d{1,2})\.(?:\s+(.*))?$")
L2 = re.compile(r"^\s*([a-z])\.(?:\s+(.*))?$")
L3 = re.compile(r"^\s*\((\d{1,2})\)(?:\s+(.*))?$")
NOISE = re.compile(r"^\s*(\d{1,3}|Page \d+.*|Mississippi Curriculum Framework.*|"
                   r"Revised.*|[A-Z][a-z]+ 20\d{2})\s*$")


def _clean_lines(block):
    out = []
    for ln in block.split("\n"):
        ln = ln.replace("\u00a0", " ").rstrip()   # NBSP -> plain space
        if not ln.strip() or NOISE.match(ln):
            continue
        out.append(ln)
    return out


def parse_slos(text):
    """Split the framework into course blocks and parse each one's nested SLO tree.

    Levels seen across 2017-2025 frameworks: `1.` -> `a.` -> `(1)`. Older frameworks add a
    "The student will" lead-in, which is dropped."""
    marks = [m.start() for m in COURSE_MARK.finditer(text)]
    courses = []
    for i, start in enumerate(marks):
        block = text[start:(marks[i + 1] if i + 1 < len(marks) else len(text))]
        lines = _clean_lines(block)

        code = title = None
        for j, ln in enumerate(lines[:10]):
            m = CODE_INLINE.match(ln)
            if m:
                code = "%s %s" % (m.group(1), m.group(2))
                title = re.sub(r"[.\s]{3,}.*$", "", m.group(3)).strip(" .")
                break
            m = CODE_ALONE.match(ln)
            if m:
                code = "%s %s" % (m.group(1), m.group(2))
                for nxt in lines[j + 1:j + 4]:      # title sits on a following line
                    if not END_MARK.match(nxt) and not CODE_ALONE.match(nxt):
                        title = re.sub(r"[.\s]{3,}.*$", "", nxt).strip(" .")
                        break
                break
        if not code:
            continue

        sm = SLO_MARK.search(block)
        outcomes = []
        if sm:
            for ln in _clean_lines(block[sm.end():]):
                if END_MARK.match(ln):
                    break
                if re.match(r"^\s*The student will\b", ln, re.I):
                    continue
                m1, m2, m3 = L1.match(ln), L2.match(ln), L3.match(ln)
                if m1:
                    outcomes.append({"n": m1.group(1), "text": (m1.group(2) or "").strip(), "sub": []})
                elif m2 and outcomes:
                    outcomes[-1]["sub"].append({"n": m2.group(1), "text": (m2.group(2) or "").strip(), "sub": []})
                elif m3 and outcomes and outcomes[-1]["sub"]:
                    outcomes[-1]["sub"][-1]["sub"].append(
                        {"n": m3.group(1), "text": (m3.group(2) or "").strip(), "sub": []})
                else:
                    # continuation: either a wrapped line, or the outcome text for a marker
                    # that sat alone on its own line
                    tgt = None
                    if outcomes:
                        tgt = outcomes[-1]
                        while tgt["sub"]:
                            tgt = tgt["sub"][-1]
                    if tgt and len(ln.strip()) > 1:
                        tgt["text"] = (tgt["text"] + " " + ln.strip()).strip()

        dm = re.search(r"Description\s*:\s*(.*?)(?:Hour Breakdown|Prerequisite|Student Learning)",
                       block, re.S | re.I)
        desc = re.sub(r"\s+", " ", dm.group(1)).strip() if dm else ""
        courses.append({"course": code, "title": title, "description": desc, "outcomes": outcomes})

    # a course can appear twice (summary table + detail); keep the richest instance
    best = {}
    for c in courses:
        k = c["course"]
        if k not in best or _leaf_count(c) > _leaf_count(best[k]):
            best[k] = c
    return [best[k] for k in sorted(best)]


def _leaves(outcomes, path=""):
    """Flatten to leaf outcomes; a leaf is what alignment is judged against."""
    out = []
    for o in outcomes:
        p = (path + "." if path else "") + str(o["n"])
        if o["sub"]:
            out.extend(_leaves(o["sub"], p))
        else:
            out.append({"id": p, "text": o["text"]})
    return out


def _leaf_count(c):
    return len(_leaves(c["outcomes"]))


def cmd_slos(a):
    try:
        import pymupdf
    except ImportError:
        try:
            import pymupdf
        except ImportError:
            print("REFUSING: PyMuPDF is required (pip install PyMuPDF).")
            return 2
    d = pymupdf.open(a.pdf)
    text = "\n".join(p.get_text() for p in d)
    courses = parse_slos(text)
    if not courses:
        print("REFUSING: parsed 0 courses from %s. Do not guess the SLOs; extract the text and "
              "read it, or the framework layout differs from every version seen so far." % a.pdf)
        return 2

    if a.course:
        want = re.sub(r"\s+", " ", a.course.strip().upper())
        wnum = re.sub(r"[^0-9]", "", want)
        exact = [c for c in courses if c["course"].upper() == want]
        bynum = [c for c in courses if re.sub(r"[^0-9]", "", c["course"]) == wnum]
        byttl = []
        if a.title:
            t = a.title.lower()
            byttl = [c for c in courses if c["title"] and
                     (t in c["title"].lower() or c["title"].lower() in t)]
        picked = exact or bynum or byttl
        match_kind = ("exact" if exact else "number-only" if bynum else "title-only" if byttl else "none")
        result = {"pdf": os.path.basename(a.pdf), "pages": len(d), "requested": a.course,
                  "match": match_kind, "courses_in_framework": len(courses),
                  "course": picked[0] if picked else None,
                  "leaves": _leaves(picked[0]["outcomes"]) if picked else []}
        if match_kind == "none":
            result["note"] = (
                "This framework defines no course matching %r by number or title. Local course "
                "numbers DO drift from state numbers, so check the full course list below by "
                "title before concluding the course is not covered." % a.course)
            result["course_list"] = [{"course": c["course"], "title": c["title"]} for c in courses]
        elif match_kind != "exact":
            result["note"] = ("Matched on %s, not on the exact code. Confirm this is the same "
                              "course before reporting alignment." % match_kind)

        # A course can appear only in a course-sequence table, with no SLO block of its own.
        # Reporting "aligned" against an empty outcome list would be vacuous, so refuse.
        if picked and not result["leaves"]:
            if a.out:
                json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2)
            print("REFUSING: %s was found in %s but carries NO Student Learning Outcomes.\n"
                  "  It may appear only in a course-sequence table, or its outcomes may live in a\n"
                  "  different framework (a course shared across programs). Open the PDF and look\n"
                  "  before reporting alignment; an empty outcome list must never be reported as\n"
                  "  'aligned'." % (picked[0]["course"], os.path.basename(a.pdf)))
            return 2
    else:
        result = {"pdf": os.path.basename(a.pdf), "pages": len(d),
                  "courses_in_framework": len(courses), "courses": courses}

    if a.out:
        json.dump(result, open(a.out, "w", encoding="utf-8"), indent=2)
        print("wrote %s" % a.out)
    if a.summary or not a.out:
        print("%s: %d pages, %d courses" % (os.path.basename(a.pdf), len(d), len(courses)))
        for c in courses:
            print("  %-10s %-52s %d outcomes / %d leaves"
                  % (c["course"], (c["title"] or "")[:52], len(c["outcomes"]), _leaf_count(c)))
    return 0


# ------------------------------------------------------------------- validate

def _load_alignment(path):
    j = json.load(open(path, encoding="utf-8-sig"))
    return j


def cmd_validate(a):
    slos = json.load(open(a.slos, encoding="utf-8-sig"))
    items = json.load(open(a.items, encoding="utf-8-sig"))
    al = _load_alignment(a.alignment)

    leaves = slos.get("leaves") or _leaves((slos.get("course") or {}).get("outcomes", []))
    leaf_ids = [L["id"] for L in leaves]
    item_ids = {str(i["id"]) for i in items.get("items", [])}

    rows = al.get("alignment") or []
    seen = [str(r.get("slo")) for r in rows]
    errors, warnings = [], []

    if not leaf_ids:
        errors.append("slos.json carries no leaf outcomes; nothing to align against")
    missing = [x for x in leaf_ids if x not in seen]
    extra = [x for x in seen if x not in leaf_ids]
    if missing:
        errors.append("alignment omits %d outcome(s): %s" % (len(missing), ", ".join(missing)))
    if extra:
        errors.append("alignment invents outcome id(s) not in the framework: %s" % ", ".join(extra))
    if len(seen) != len(set(seen)):
        errors.append("alignment lists the same outcome more than once")

    for r in rows:
        sid = r.get("slo")
        v = r.get("verdict")
        if v not in VERDICTS:
            errors.append("outcome %s: verdict %r is not one of %s" % (sid, v, ", ".join(VERDICTS)))
        ev = [str(x) for x in (r.get("evidence") or [])]
        bad = [x for x in ev if x not in item_ids]
        if bad:
            errors.append("outcome %s: cites item id(s) not in the course: %s" % (sid, ", ".join(bad)))
        if v in ("assessed", "partially-assessed") and not ev:
            errors.append("outcome %s: verdict %r with no evidence cited" % (sid, v))
        if v == "not-assessed" and ev:
            warnings.append("outcome %s: verdict not-assessed but evidence is cited" % sid)
        if not (r.get("rationale") or "").strip():
            warnings.append("outcome %s: no rationale given" % sid)

    covered = sum(1 for r in rows if r.get("verdict") == "assessed")
    partial = sum(1 for r in rows if r.get("verdict") == "partially-assessed")
    gaps = [r for r in rows if r.get("verdict") in ("not-assessed", "ungraded-only")]

    print("outcomes in framework : %d" % len(leaf_ids))
    print("outcomes mapped       : %d" % len(rows))
    print("assessed              : %d" % covered)
    print("partially assessed    : %d" % partial)
    print("gaps                  : %d" % len(gaps))
    for w in warnings:
        print("  warn: %s" % w)
    if errors:
        print("\nERRORS (%d):" % len(errors))
        for e in errors:
            print("  - %s" % e)
        print("\nVALIDATE: FAILED")
        return 2
    print("\nVALIDATE: ok")
    return 0


# --------------------------------------------------------------------- report

VERDICT_LABEL = {
    "assessed": "Assessed",
    "partially-assessed": "Partially assessed",
    "ungraded-only": "Ungraded coverage only",
    "not-assessed": "Not assessed",
    "not-applicable": "Not applicable",
}


def cmd_report(a):
    slos = json.load(open(a.slos, encoding="utf-8-sig"))
    items = json.load(open(a.items, encoding="utf-8-sig"))
    al = _load_alignment(a.alignment)
    rows = al.get("alignment") or []
    by_id = {str(i["id"]): i for i in items.get("items", [])}
    course = (slos.get("course") or {})
    leaves = {L["id"]: L["text"] for L in (slos.get("leaves") or _leaves(course.get("outcomes", [])))}

    n = len(rows)
    cnt = {v: sum(1 for r in rows if r.get("verdict") == v) for v in VERDICTS}
    scored = cnt["assessed"] + 0.5 * cnt["partially-assessed"]
    pct = (100.0 * scored / n) if n else 0.0

    def nm(i):
        it = by_id.get(str(i))
        return "%s (%s)" % (it["name"], it["type"]) if it else str(i)

    # ---------- markdown
    md = []
    md.append("# SLO alignment: %s %s" % (course.get("course", "?"), course.get("title", "")))
    md.append("")
    md.append("**Canvas course:** %s (id %s)" % (items.get("course_name", "?"), items.get("course_id", "?")))
    md.append("**Framework:** %s%s" % (al.get("framework", slos.get("pdf", "?")),
                                       (" (%s)" % al.get("framework_year")) if al.get("framework_year") else ""))
    md.append("**Program:** %s" % al.get("program", "?"))
    md.append("**Source:** %s" % al.get("framework_url", "see framework-sources.json"))
    md.append("")
    md.append("Coverage: **%.0f%%** of outcomes assessed (%d assessed, %d partial, %d ungraded-only, "
              "%d not assessed, of %d)." % (pct, cnt["assessed"], cnt["partially-assessed"],
                                            cnt["ungraded-only"], cnt["not-assessed"], n))
    md.append("")
    md.append("| Outcome | Student Learning Outcome | Verdict | Evidence |")
    md.append("|---|---|---|---|")
    for r in rows:
        md.append("| %s | %s | %s | %s |" % (
            r.get("slo"), leaves.get(str(r.get("slo")), "")[:150],
            VERDICT_LABEL.get(r.get("verdict"), r.get("verdict")),
            "; ".join(nm(x) for x in (r.get("evidence") or [])) or "none"))
    gaps = [r for r in rows if r.get("verdict") in ("not-assessed", "ungraded-only")]
    if gaps:
        md.append("")
        md.append("## Gaps and suggestions")
        for r in gaps:
            md.append("")
            md.append("### %s %s" % (r.get("slo"), leaves.get(str(r.get("slo")), "")))
            md.append("")
            md.append("**Why it is a gap:** %s" % (r.get("rationale") or "").strip())
            if r.get("suggestion"):
                md.append("")
                md.append("**Suggested fix:** %s" % r["suggestion"].strip())
    unaligned = [i for i in items.get("items", [])
                 if i.get("graded") and str(i["id"]) not in
                 {str(x) for r in rows for x in (r.get("evidence") or [])}]
    if unaligned:
        md.append("")
        md.append("## Graded items mapped to no outcome")
        md.append("")
        md.append("Not necessarily wrong; an instructor may teach beyond the state floor.")
        md.append("")
        for i in unaligned:
            md.append("- %s (%s, %s pts)" % (i["name"], i["type"], i.get("points")))
    md.append("")
    md.append("---")
    md.append("")
    md.append("Generated by CourseForge from the MCCB curriculum framework. Verify against the "
              "framework version your college is running before using this for program review.")
    md_text = "\n".join(md)

    if a.out_md:
        open(a.out_md, "w", encoding="utf-8").write(md_text)
        print("wrote %s" % a.out_md)

    # ---------- Canvas-safe HTML (clean look: borders and navy headings, no fills)
    if a.out_html:
        e = lambda s: html.escape(str(s), quote=False)
        H = []
        H.append('<div style="max-width: 980px; margin: 0 auto; font-family: Inter, \'Segoe UI\', '
                 'Roboto, Helvetica, Arial, sans-serif; line-height: 1.55;">')
        H.append('<div style="padding: 24px; border-radius: 8px; border-top: 5px solid #E9A821;">')
        H.append('<div style="font-size: 13px;">%s &middot; SLO ALIGNMENT</div>' % e(course.get("course", "")))
        H.append('<h2 style="margin: 6px 0 4px; font-size: 30px; font-family: Georgia, \'Times New Roman\', '
                 'serif; color: #061E3F;">%s</h2>' % e(course.get("title") or "SLO alignment"))
        H.append('<p style="margin: 0 0 14px; font-size: 15px;">%s outcomes assessed, from the %s '
                 'Mississippi Curriculum Framework for %s.</p>'
                 % (e("%.0f%%" % pct), e(al.get("framework_year", "current")), e(al.get("program", "this program"))))
        H.append("</div>")
        H.append('<div style="margin-top: 18px; padding: 10px 12px; border-radius: 8px; border-left: '
                 '4px solid #236192; font-size: 13px;"><strong>Summary:</strong> %d assessed, %d partially '
                 'assessed, %d covered only by ungraded material, %d not assessed, of %d outcomes.</div>'
                 % (cnt["assessed"], cnt["partially-assessed"], cnt["ungraded-only"], cnt["not-assessed"], n))
        H.append('<div style="margin-top: 18px; padding: 18px; border-radius: 8px; border: 1px solid '
                 '#d7dce3; border-top: 4px solid #E9A821;">')
        H.append('<h3 style="margin: 0 0 12px; font-size: 19px; color: #061E3F;"><span style="border-bottom: '
                 '2px solid #E9A821; padding-bottom: 6px;">Outcome coverage</span></h3>')
        H.append('<table style="border-collapse: collapse; width: 100%; font-size: 14px;">')
        H.append('<tr><th scope="col" style="border: 1px solid #d7dce3; padding: 6px; text-align: left;">Outcome</th>'
                 '<th scope="col" style="border: 1px solid #d7dce3; padding: 6px; text-align: left;">Student Learning Outcome</th>'
                 '<th scope="col" style="border: 1px solid #d7dce3; padding: 6px; text-align: left;">Verdict</th>'
                 '<th scope="col" style="border: 1px solid #d7dce3; padding: 6px; text-align: left;">Evidence</th></tr>')
        for r in rows:
            H.append('<tr><th scope="row" style="border: 1px solid #d7dce3; padding: 6px;">%s</th>'
                     '<td style="border: 1px solid #d7dce3; padding: 6px;">%s</td>'
                     '<td style="border: 1px solid #d7dce3; padding: 6px;">%s</td>'
                     '<td style="border: 1px solid #d7dce3; padding: 6px;">%s</td></tr>'
                     % (e(r.get("slo")), e(leaves.get(str(r.get("slo")), "")),
                        e(VERDICT_LABEL.get(r.get("verdict"), r.get("verdict"))),
                        e("; ".join(nm(x) for x in (r.get("evidence") or [])) or "none")))
        H.append("</table></div>")
        if gaps:
            H.append('<div style="margin-top: 18px; padding: 14px 16px; border-radius: 8px; border: 1px solid '
                     '#f3c2c8; border-left: 5px solid #C11F31;">')
            H.append('<div style="font-size: 14px; color: #C11F31; font-weight: 700; margin-bottom: 4px;">'
                     '&#9888; Gaps to close</div>')
            H.append('<ul style="margin: 0; padding-left: 18px; font-size: 14px;">')
            for r in gaps:
                H.append("<li><strong>%s</strong> %s %s</li>" % (
                    e(r.get("slo")), e(leaves.get(str(r.get("slo")), "")),
                    ("&mdash; " + e(r["suggestion"])) if r.get("suggestion") else ""))
            H.append("</ul></div>")
        H.append('<div style="margin-top: 18px; padding: 16px 18px; border-radius: 8px; border-top: '
                 '5px solid #E9A821;"><div style="font-size: 13px;">Generated by CourseForge from the '
                 'MCCB curriculum framework. Confirm the framework version your college is running '
                 'before using this for program review.</div></div>')
        H.append("</div>")
        open(a.out_html, "w", encoding="utf-8").write("\n".join(H))
        print("wrote %s" % a.out_html)

    print("\ncoverage %.0f%%  (%d assessed, %d partial, %d ungraded-only, %d not assessed, of %d)"
          % (pct, cnt["assessed"], cnt["partially-assessed"], cnt["ungraded-only"], cnt["not-assessed"], n))
    return 0


# ------------------------------------------------------------------------ cli

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="fetch/cache the MCCB program index")
    p.add_argument("--cache"); p.add_argument("--refresh", action="store_true")
    p.add_argument("--list-prefixes", action="store_true"); p.set_defaults(fn=cmd_index)

    p = sub.add_parser("resolve", help="course code -> candidate programs")
    p.add_argument("--course-code"); p.add_argument("--cip"); p.add_argument("--name")
    p.add_argument("--cache"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("fetch", help="download a program's framework PDFs")
    p.add_argument("--slug", required=True); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("slos", help="extract courses + SLOs from a framework PDF")
    p.add_argument("--pdf", required=True); p.add_argument("--course"); p.add_argument("--title")
    p.add_argument("--out"); p.add_argument("--summary", action="store_true")
    p.set_defaults(fn=cmd_slos)

    p = sub.add_parser("validate", help="check an agent-written alignment.json")
    p.add_argument("--alignment", required=True); p.add_argument("--slos", required=True)
    p.add_argument("--items", required=True); p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("report", help="render the alignment report")
    p.add_argument("--alignment", required=True); p.add_argument("--slos", required=True)
    p.add_argument("--items", required=True)
    p.add_argument("--out-md"); p.add_argument("--out-html")
    p.set_defaults(fn=cmd_report)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
