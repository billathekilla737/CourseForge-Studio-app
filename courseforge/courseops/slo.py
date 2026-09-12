"""Student Learning Outcome alignment, as four steps a person walks through.

`slo_framework.py` is the mechanical half and keeps its own command line. This
module is the half the Studio talks to: it hands the framework tool real file
paths, inventories the Canvas course, asks Claude for the judgment in between,
refuses to believe the answer until it validates, and pushes the report as an
unpublished page behind the confirm gate.

    resolve   a course code -> candidate programs (ambiguity becomes choices)
    fetch     the framework PDF, that course's outcomes, and what the course holds
    align     Claude maps every outcome to evidence, strict JSON, validated
    validate  the honesty gate: the same rules the framework tool exits 2 on
    report    markdown plus Canvas-safe HTML, pushed to one fixed page slug

The deliberate split from slo-alignment.md is kept: scripts collect facts and
check the result, the model does the judging in between. `validate` is what
stops the judging from hand-waving, so nothing here writes a report that has
not passed it.

Item ids in items.json are `<kind>-<canvas id>` (`assignment-15977654`,
`page-week-1-overview`). Prefixed because a page slug and a quiz id do not
share a number space, and an alignment that cites the wrong one must be
catchable rather than plausible.
"""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from types import SimpleNamespace

from .. import ledger, llm
from ..style import HUMANIZE_RULES
from . import slo_framework as fw
from .common import (AREA, Gate, Log, Refused, area_dir, course_label, load_json,
                     now_iso, plural, quiet_log, save_json, strip_html)

VERDICTS = fw.VERDICTS
REPORT_SLUG = "slo-alignment-report-instructor-only"
REPORT_TITLE = "SLO alignment report (instructor only)"
MARKER = "SLO ALIGNMENT"

INDEX_FILE = "index.json"
RESOLVE_FILE = "resolve.json"
ITEMS_FILE = "items.json"
SLOS_FILE = "slos.json"
ALIGNMENT_FILE = "alignment.json"
REPORT_MD = "report.md"
REPORT_HTML = "report.html"
PUSH_FILE = "last-push.json"
EXCERPT_CHARS = 400

SYSTEM = (
    "You align a Canvas course against the Student Learning Outcomes of a Mississippi "
    "Curriculum Framework. You answer with one JSON object and nothing else: no prose "
    "before it, no code fence around it.\n\n" + HUMANIZE_RULES
)


# --------------------------------------------------------------- where things live
def slo_dir(app, course_id) -> Path:
    path = area_dir(app, course_id) / "slo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def framework_dir(app, course_id) -> Path:
    path = slo_dir(app, course_id) / "framework"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pdf_reader_ok() -> bool:
    """The framework PDFs are read with PyMuPDF. A web-to-markdown fetch mangles
    them, so there is no fallback: the area says what to install instead."""
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


NO_PDF_READER = ("PyMuPDF is not installed, so the framework PDF cannot be read. Install it with "
                 "python -m pip install pymupdf, then run this again. Nothing was changed.")


def _capture(fn, *args, log: Log = quiet_log) -> int:
    """Run one of the framework tool's cmd_* functions and send what it prints
    to the job log instead of the server's stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(*args)
    for line in buf.getvalue().splitlines():
        if line.strip():
            log(line.rstrip())
    return int(code or 0)


# ----------------------------------------------------------------------- resolve
def program_index(app=None, course_id=None, refresh: bool = False) -> list[dict]:
    """The MCCB program list, cached on disk beside the course when there is
    one. The framework tool refuses on a parse of zero programs, and so does
    this: an empty index means the site markup moved, not that the program is
    missing."""
    cache = None
    if app is not None and course_id is not None:
        cache = slo_dir(app, course_id) / INDEX_FILE
    if cache and cache.is_file() and not refresh:
        cached = load_json(cache, {}) or {}
        if cached.get("programs"):
            return cached["programs"]
    programs = fw.parse_index(fw._get(fw.INDEX_URL))
    if not programs:
        raise Refused(
            "The MCCB program index came back with no programs in it, so the page layout has "
            "changed. Nothing was guessed. Open https://www.mccb.edu/curriculum and check it "
            "before trusting any alignment result.")
    if cache:
        save_json(cache, {"index_url": fw.INDEX_URL, "at": now_iso(),
                          "count": len(programs), "programs": programs})
    return programs


def split_code(course_code: str | None) -> tuple[str | None, str | None]:
    """'ATT 1214 001' -> ('ATT', '1214'). The section number is dropped."""
    m = re.match(r"\s*([A-Za-z]{2,4})\s*(\d{3,4})?", str(course_code or ""))
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2)


def resolve(course_code: str | None = None, programs: list[dict] | None = None,
            cip: str | None = None, name: str | None = None,
            app=None, course_id=None, refresh: bool = False) -> dict:
    """Candidate programs for a course code. One hit is an answer; several are
    choices for the person to pick from; none is usually an academic-transfer
    course and is said so rather than forced into a match."""
    if programs is None:
        programs = program_index(app, course_id, refresh)
    prefix, number = split_code(course_code)

    hits = list(programs)
    if cip:
        hits = [p for p in hits if p.get("cip") == cip]
    if name:
        hits = [p for p in hits if name.lower() in (p.get("name") or "").lower()]
    if prefix and not cip and not name:
        hits = [p for p in hits if prefix in (p.get("prefixes") or [])]

    if not hits:
        conclusion = (
            f"No Mississippi framework claims the prefix {prefix or '?'}. Academic transfer "
            "courses (English, maths, history, most computer science) are governed by the "
            "statewide articulation agreement and common course numbering, not by these "
            "frameworks. There is nothing here to align against.")
    elif len(hits) > 1:
        conclusion = (
            f"{len(hits)} programs share the prefix {prefix}. Pick the one this course belongs "
            "to, or fetch each and match on the course number and title. Nothing is guessed.")
    else:
        conclusion = f"One match: {hits[0]['name']}."

    out = {
        "query": {"course_code": course_code, "prefix": prefix, "number": number,
                  "cip": cip, "name": name},
        "candidates": hits,
        "choices": [{"slug": p.get("slug"), "name": p.get("name"), "cip": p.get("cip"),
                     "prefixes": p.get("prefixes"), "url": p.get("url")} for p in hits],
        "ambiguous": len(hits) > 1,
        "resolved": hits[0] if len(hits) == 1 else None,
        "programs_in_index": len(programs),
        "conclusion": conclusion,
        "at": now_iso(),
    }
    if app is not None and course_id is not None:
        save_json(slo_dir(app, course_id) / RESOLVE_FILE, out)
    return out


# --------------------------------------------------------------------- inventory
def inventory(app, course_id, log: Log = quiet_log) -> dict:
    """What the course holds, as the evidence the alignment may cite. Reads
    only, and only through app.content, which cannot reach student work."""
    c = app.content
    course = c.course_detail(course_id) or {}
    items: list[dict] = []

    def add(kind, ident, name, *, points=None, graded=False, url=None, published=None,
            body=None, position=None):
        items.append({
            "id": f"{kind}-{ident}", "canvas_id": ident, "type": kind, "name": name or "",
            "points": points, "graded": bool(graded), "url": url, "published": published,
            "position": position,
            "excerpt": strip_html(body)[:EXCERPT_CHARS] if body else "",
        })

    log("reading the assignments", 0, 5)
    quiz_backed: set[str] = set()
    for a in c.assignments_content(course_id):
        if a.get("quiz_id"):
            quiz_backed.add(str(a["quiz_id"]))
            continue
        add("assignment", a.get("id"), a.get("name"),
            points=a.get("points_possible"),
            graded=(a.get("grading_type") != "not_graded"),
            url=a.get("html_url"), published=a.get("published"),
            body=a.get("description"), position=a.get("position"))

    log("reading the quizzes", 1, 5)
    for q in c.quizzes_content(course_id):
        add("quiz", q.get("id"), q.get("title"),
            points=q.get("points_possible"),
            graded=(q.get("quiz_type") in ("assignment", "graded_survey")
                    or str(q.get("id")) in quiz_backed),
            url=q.get("html_url"), published=q.get("published"),
            body=q.get("description"))

    log("reading the discussions", 2, 5)
    for d in c.discussions(course_id):
        assignment = d.get("assignment") or {}
        add("discussion", d.get("id"), d.get("title"),
            points=assignment.get("points_possible") or d.get("points_possible"),
            graded=bool(d.get("assignment_id") or assignment.get("id")),
            url=d.get("html_url"), published=d.get("published"),
            body=d.get("message"))

    log("reading the pages", 3, 5)
    for p in c.pages(course_id):
        slug = p.get("url")
        if not slug:
            continue
        add("page", slug, p.get("title"), graded=False,
            url=p.get("html_url"), published=p.get("published"))

    log("reading the modules", 4, 5)
    modules = []
    for m in c.modules(course_id, include_items=True):
        modules.append({
            "id": m.get("id"), "name": m.get("name"), "position": m.get("position"),
            "published": m.get("published"),
            "items": [{"title": it.get("title"), "type": it.get("type"),
                       "content_id": it.get("content_id"), "page_url": it.get("page_url")}
                      for it in (m.get("items") or [])],
        })

    graded = sum(1 for i in items if i["graded"])
    out = {
        "course_id": str(course_id),
        "course_name": course.get("name"),
        "course_code": course.get("course_code"),
        "items": items,
        "modules": modules,
        "counts": {"items": len(items), "graded": graded, "modules": len(modules),
                   "assignments": sum(1 for i in items if i["type"] == "assignment"),
                   "quizzes": sum(1 for i in items if i["type"] == "quiz"),
                   "discussions": sum(1 for i in items if i["type"] == "discussion"),
                   "pages": sum(1 for i in items if i["type"] == "page")},
        "at": now_iso(),
    }
    log(f"{len(items)} items, {graded} of them graded, in {len(modules)} modules", 5, 5)
    return out


# ----------------------------------------------------------------- framework PDFs
def fetch_framework(app, course_id, slug: str, log: Log = quiet_log) -> dict:
    """Download the program's framework PDFs into the course folder."""
    out = framework_dir(app, course_id)
    args = SimpleNamespace(slug=slug, out=str(out))
    code = _capture(fw.cmd_fetch, args, log=log)
    sources = load_json(out / "framework-sources.json", {}) or {}
    if code != 0 or not sources.get("pdfs"):
        raise Refused(
            f"No framework PDF could be downloaded from the program page for {slug}. Open the "
            "page in a browser and check that it still carries one. Nothing was written.")
    return sources


def pick_pdf(sources: dict, use_prior: bool = False) -> dict | None:
    pdfs = (sources or {}).get("pdfs") or []
    wanted = [p for p in pdfs if bool(p.get("current")) != bool(use_prior)]
    return (wanted or pdfs or [None])[0]


def slos(pdf: str | Path, course: str | None = None, title: str | None = None,
         out: str | Path | None = None, log: Log = quiet_log) -> dict:
    """One course's outcomes out of a framework PDF, through the framework
    tool's parser. Raises Refused on the two cases it exits 2 for: nothing
    parsed at all, and a course that carries no outcomes."""
    if not pdf_reader_ok():
        raise Refused(NO_PDF_READER)
    args = SimpleNamespace(pdf=str(pdf), course=course, title=title,
                           out=str(out) if out else None, summary=False)
    code = _capture(fw.cmd_slos, args, log=log)
    data = load_json(Path(out), None) if out else None
    if code != 0:
        if data and data.get("course") and not data.get("leaves"):
            raise Refused(
                f"{(data.get('course') or {}).get('course', course)} is in this framework but "
                "carries no Student Learning Outcomes. It may appear only in a course sequence "
                "table, or its outcomes may live in another program's framework. An empty "
                "outcome list is never reported as aligned.")
        raise Refused(
            f"No courses could be parsed out of {Path(pdf).name}. The framework layout differs "
            "from every version seen so far. Nothing was guessed.")
    if data is None:
        raise Refused("The outcomes were parsed but could not be written to disk.")
    return data


def leaf_outcomes(slos_data: dict) -> list[dict]:
    """[{id, text}] for the outcomes an alignment is judged against."""
    leaves = (slos_data or {}).get("leaves")
    if leaves:
        return list(leaves)
    course = (slos_data or {}).get("course") or {}
    return fw._leaves(course.get("outcomes") or [])


# --------------------------------------------------------------------- the fetch
def fetch(app, course_id, slug: str | None = None, course_code: str | None = None,
          title: str | None = None, use_prior: bool = False, log: Log = quiet_log) -> dict:
    """Inventory the course, resolve the program if it was not picked, download
    the framework and extract this course's outcomes. Nothing is written to
    Canvas."""
    if not pdf_reader_ok():
        raise Refused(NO_PDF_READER)
    folder = slo_dir(app, course_id)
    items = inventory(app, course_id, log)
    save_json(folder / ITEMS_FILE, items)

    code = course_code or items.get("course_code") or ""
    if not slug:
        log(f"resolving {code or 'this course'} to a program")
        found = resolve(code, app=app, course_id=course_id)
        if found["ambiguous"]:
            raise Refused(found["conclusion"])
        if not found["resolved"]:
            raise Refused(found["conclusion"])
        slug = found["resolved"].get("slug")
        program = found["resolved"]
    else:
        saved = load_json(folder / RESOLVE_FILE, {}) or {}
        program = next((p for p in (saved.get("candidates") or [])
                        if str(p.get("slug")) == str(slug)), {"slug": slug, "name": slug})

    log(f"downloading the framework for {program.get('name') or slug}")
    sources = fetch_framework(app, course_id, slug, log)
    pdf = pick_pdf(sources, use_prior)
    if not pdf:
        raise Refused("The program page carried no framework PDF to read.")

    wanted = (code or "").strip()
    m = re.match(r"\s*([A-Za-z]{2,4})\s*(\d{3,4})", wanted)
    wanted = f"{m.group(1).upper()} {m.group(2)}" if m else wanted
    log(f"reading the outcomes for {wanted or 'every course'} out of {Path(pdf['path']).name}")
    parsed = slos(pdf["path"], wanted or None, title or items.get("course_name"),
                  folder / SLOS_FILE, log)

    out = {
        "program": {"slug": slug, "name": program.get("name"), "cip": program.get("cip"),
                    "url": program.get("url")},
        "framework": {"path": pdf.get("path"), "name": Path(pdf["path"]).name,
                      "url": pdf.get("url"), "year": pdf.get("year"),
                      "current": bool(pdf.get("current")), "page": sources.get("page")},
        "course": parsed.get("course"),
        "match": parsed.get("match"),
        "note": parsed.get("note"),
        "outcomes": len(leaf_outcomes(parsed)),
        "items": items["counts"],
        "at": now_iso(),
    }
    save_json(folder / "fetch.json", out)
    log(f"{out['outcomes']} outcomes to align against, "
        f"{items['counts']['graded']} graded items to align them to. Nothing is pushed.")
    return out


# ------------------------------------------------------------------------ align
def build_prompt(slos_data: dict, items: dict, notes: str = "") -> str:
    leaves = leaf_outcomes(slos_data)
    course = (slos_data or {}).get("course") or {}
    lines = [
        f"Canvas course: {items.get('course_name')} ({items.get('course_code') or 'no code'}), "
        f"id {items.get('course_id')}.",
        f"Framework course: {course.get('course')} {course.get('title') or ''}".rstrip(),
        "",
        "THE OUTCOMES. Every one of these must appear exactly once in your answer, under the "
        "id given here. Do not invent an id, do not merge two, do not leave one out.",
    ]
    for leaf in leaves:
        lines.append(f"  {leaf['id']}  {leaf['text']}")
    lines += [
        "",
        "WHAT THE COURSE HOLDS. Cite evidence only by these ids, exactly as written.",
        "  id | graded | points | type | name | first lines of the description",
    ]
    for it in items.get("items") or []:
        lines.append("  {id} | {g} | {p} | {t} | {n} | {x}".format(
            id=it["id"], g="graded" if it.get("graded") else "ungraded",
            p=("" if it.get("points") in (None, "") else it["points"]),
            t=it.get("type"), n=(it.get("name") or "")[:90],
            x=re.sub(r"\s+", " ", it.get("excerpt") or "")[:220]))
    lines += [
        "",
        "HOW TO JUDGE. Read the verb in the outcome literally. An outcome that says create, "
        "implement, compile, demonstrate or produce is not satisfied by an essay about the "
        "topic. Pick one verdict per outcome, from this list and no other:",
        "  assessed            a graded item requires the thing the verb demands",
        "  partially-assessed  part of the outcome is met and part is not; say which half",
        "  ungraded-only       a reading or ungraded activity covers it, nothing scored does",
        "  not-assessed        no item covers it",
        "  not-applicable      genuinely out of scope for this course; rare, and justify it",
        "",
        "assessed and partially-assessed must each cite at least one evidence id. not-assessed "
        "cites none. Write a rationale for every outcome, and a suggestion for every gap "
        "(not-assessed and ungraded-only): one concrete thing to add or change.",
        "",
        "ANSWER with this JSON object and nothing else:",
        '{"program": "...", "framework": "...pdf", "framework_year": 2025, '
        '"framework_url": "https://...", "alignment": [{"slo": "1.a", "verdict": "assessed", '
        '"evidence": ["assignment-123"], "rationale": "...", "suggestion": "..."}]}',
    ]
    if notes:
        lines += ["", "YOUR LAST ANSWER WAS REJECTED. Fix exactly these problems and answer again:",
                  notes]
    return "\n".join(lines)


def align(app, course_id, log: Log = quiet_log, model: str | None = None,
          retries: int = 1) -> dict:
    """Claude maps every outcome to evidence. The answer is validated before it
    is believed; a failing answer is shown its own errors once and asked again.
    Nothing here touches Canvas."""
    folder = slo_dir(app, course_id)
    slos_data = load_json(folder / SLOS_FILE)
    items = load_json(folder / ITEMS_FILE)
    if not slos_data or not items:
        raise Refused("Fetch the framework and the course inventory first; there is nothing to "
                      "align yet.")
    leaves = leaf_outcomes(slos_data)
    if not leaves:
        raise Refused("This course carries no outcomes in the framework, so there is nothing to "
                      "align against.")
    fetched = load_json(folder / "fetch.json", {}) or {}
    model = model or getattr(app.cfg, "model", "opus") or "opus"

    notes = ""
    result = None
    data: dict = {}
    problems: dict = {}
    for attempt in range(1 + max(0, int(retries))):
        log(f"asking Claude to judge {plural(len(leaves), 'outcome')} against "
            f"{plural(len(items.get('items') or []), 'item')}"
            + (f" (attempt {attempt + 1})" if attempt else ""), attempt, 1 + max(0, int(retries)))
        result = llm.run(build_prompt(slos_data, items, notes), model=model, system=SYSTEM,
                         expect_json=True, timeout_s=900)
        data = result.data if isinstance(result.data, dict) else {}
        if not data:
            notes = "Your answer was not a JSON object. Answer with the object alone."
            problems = {"errors": ["Claude did not answer with JSON."], "warnings": [], "ok": False}
            continue
        data.setdefault("program", (fetched.get("program") or {}).get("name"))
        data.setdefault("framework", (fetched.get("framework") or {}).get("name"))
        data.setdefault("framework_year", (fetched.get("framework") or {}).get("year"))
        data.setdefault("framework_url", (fetched.get("framework") or {}).get("url"))
        problems = validate(slos_data, items, data)
        if problems["ok"]:
            break
        notes = "\n".join("  - " + e for e in problems["errors"])
        log(f"the answer failed the check: {problems['errors'][0]}")

    data["at"] = now_iso()
    data["model"] = model
    data["cost_usd"] = round(getattr(result, "cost_usd", 0.0) or 0.0, 4)
    save_json(folder / ALIGNMENT_FILE, data)
    out = {"alignment": data, "validate": problems, "counts": problems.get("counts") or {},
           "cost_usd": data["cost_usd"], "ok": bool(problems.get("ok"))}
    if problems.get("ok"):
        log(f"{problems['counts']['assessed']} outcomes assessed, "
            f"{problems['counts']['partial']} partly, {problems['counts']['gaps']} gaps. "
            "Nothing is pushed.")
    else:
        log(f"the alignment is saved but does not pass the check ({len(problems['errors'])} "
            "problems). No report can be written from it.")
    return out


# --------------------------------------------------------------------- validate
def validate(slos_data: dict, items: dict, alignment: dict) -> dict:
    """The honesty gate, the same rules the framework tool exits 2 on: an
    omitted outcome, an invented outcome id, a duplicate, a verdict outside the
    fixed set, an evidence id that is not in items.json, and coverage claimed
    with no evidence at all."""
    leaves = leaf_outcomes(slos_data)
    leaf_ids = [str(leaf["id"]) for leaf in leaves]
    item_ids = {str(i.get("id")) for i in (items or {}).get("items") or []}
    rows = (alignment or {}).get("alignment") or []
    seen = [str(r.get("slo")) for r in rows]

    errors: list[str] = []
    warnings: list[str] = []

    if not leaf_ids:
        errors.append("The framework carries no outcomes for this course, so there is nothing "
                      "to align against.")
    missing = [x for x in leaf_ids if x not in seen]
    invented = [x for x in seen if x not in leaf_ids]
    duplicates = sorted({x for x in seen if seen.count(x) > 1})
    if missing:
        errors.append(f"{plural(len(missing), 'outcome')} left out: {', '.join(missing)}")
    if invented:
        errors.append("outcome ids that are not in the framework: " + ", ".join(invented))
    if duplicates:
        errors.append("the same outcome is listed more than once: " + ", ".join(duplicates))

    for r in rows:
        sid = r.get("slo")
        verdict = r.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"outcome {sid}: {verdict!r} is not one of {', '.join(VERDICTS)}")
        evidence = [str(x) for x in (r.get("evidence") or [])]
        stray = [x for x in evidence if x not in item_ids]
        if stray:
            errors.append(f"outcome {sid}: cites items that are not in this course: "
                          + ", ".join(stray))
        if verdict in ("assessed", "partially-assessed") and not evidence:
            errors.append(f"outcome {sid}: {verdict} with nothing cited as evidence")
        if verdict == "not-assessed" and evidence:
            warnings.append(f"outcome {sid}: not assessed, but evidence is cited")
        if verdict in ("not-assessed", "ungraded-only") and not (r.get("suggestion") or "").strip():
            warnings.append(f"outcome {sid}: a gap with no suggested fix")
        if not (r.get("rationale") or "").strip():
            warnings.append(f"outcome {sid}: no reason given")

    counts = {v: sum(1 for r in rows if r.get("verdict") == v) for v in VERDICTS}
    counts["outcomes"] = len(leaf_ids)
    counts["mapped"] = len(rows)
    counts["partial"] = counts["partially-assessed"]
    counts["gaps"] = counts["not-assessed"] + counts["ungraded-only"]
    counts["coverage"] = round(
        100.0 * (counts["assessed"] + 0.5 * counts["partially-assessed"]) / len(rows), 1
    ) if rows else 0.0
    return {"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts,
            "missing": missing, "invented": invented, "duplicates": duplicates}


def validate_saved(app, course_id) -> dict:
    folder = slo_dir(app, course_id)
    slos_data = load_json(folder / SLOS_FILE)
    items = load_json(folder / ITEMS_FILE)
    alignment = load_json(folder / ALIGNMENT_FILE)
    if not (slos_data and items and alignment):
        raise Refused("There is no alignment to check yet. Fetch, then align.")
    return validate(slos_data, items, alignment)


# ----------------------------------------------------------------------- report
def render(app, course_id, log: Log = quiet_log) -> dict:
    """Markdown for program review, Canvas-safe HTML for the course. Local
    files only."""
    folder = slo_dir(app, course_id)
    problems = validate_saved(app, course_id)
    if not problems["ok"]:
        raise Refused("The alignment does not pass the check, so no report is written: "
                      + problems["errors"][0])
    args = SimpleNamespace(slos=str(folder / SLOS_FILE), items=str(folder / ITEMS_FILE),
                           alignment=str(folder / ALIGNMENT_FILE),
                           out_md=str(folder / REPORT_MD), out_html=str(folder / REPORT_HTML))
    _capture(fw.cmd_report, args, log=log)
    html = (folder / REPORT_HTML).read_text(encoding="utf-8")
    return {"html": html, "md_path": str(folder / REPORT_MD),
            "html_path": str(folder / REPORT_HTML), "chars": len(html),
            "counts": problems["counts"], "warnings": problems["warnings"]}


def report_sentence(label: str, counts: dict, exists: bool) -> str:
    what = ("Replace the body of the unpublished page" if exists
            else "Create an unpublished page named")
    return (f"{what} {REPORT_TITLE} in {label}. It holds every outcome, the verdict for each, "
            f"the evidence named, and {plural(counts.get('gaps', 0), 'gap')} with a suggested "
            "fix. The page stays unpublished, so students cannot see it. Nothing else in the "
            "course is changed.")


def find_report_page(app, course_id) -> dict | None:
    try:
        return app.content.page(course_id, REPORT_SLUG)
    except Exception:  # noqa: BLE001  (a 404 is the normal first-run answer)
        return None


def report(app, course_id, apply: bool = False, gate: Gate | None = None,
           log: Log = quiet_log) -> dict:
    """Render, and with apply=True push the HTML to the one fixed page slug as
    an unpublished page, behind the confirm gate. Written, read back, compared."""
    rendered = render(app, course_id, log)
    c = app.content
    course = c.course_detail(course_id) or {}
    label = course_label(course, course_id)
    existing = find_report_page(app, course_id)
    counts = rendered["counts"]
    sentence = report_sentence(label, counts, bool(existing))
    detail = [
        {"label": "Page", "from": "unpublished" if existing else "does not exist yet",
         "to": "unpublished"},
        {"label": "Outcomes", "from": str(counts.get("outcomes", 0)),
         "to": f"{counts.get('assessed', 0)} assessed, {counts.get('partial', 0)} partly, "
               f"{counts.get('gaps', 0)} gaps"},
        {"label": "Coverage", "from": "", "to": f"{counts.get('coverage', 0)}%"},
    ]
    plan = {"applied": False, "sentence": sentence, "detail": detail, "counts": counts,
            "exists": bool(existing), "slug": REPORT_SLUG, "title": REPORT_TITLE,
            "chars": rendered["chars"], "md_path": rendered["md_path"],
            "html_path": rendered["html_path"], "warnings": rendered["warnings"]}
    if not apply:
        return plan
    if gate is None:
        raise ValueError("pushing the report needs a gate")
    gate("courseops.slo_report",
         {"course_id": str(course_id), "slug": REPORT_SLUG, "chars": rendered["chars"],
          "coverage": counts.get("coverage")},
         sentence, detail)

    log(f"writing the page {REPORT_SLUG} (unpublished)")
    written = c.update_page(course_id, REPORT_SLUG, body=rendered["html"],
                            title=REPORT_TITLE, published=False) or {}
    slug = written.get("url") or REPORT_SLUG
    back = {}
    try:
        back = c.page(course_id, slug) or {}
    except Exception as exc:  # noqa: BLE001
        log(f"could not read the page back: {type(exc).__name__}: {exc}")
    body_back = back.get("body") or ""
    ok = MARKER in body_back and abs(len(body_back) - rendered["chars"]) <= max(
        40, rendered["chars"] // 20)
    published = bool(back.get("published", written.get("published")))
    warnings = list(rendered["warnings"])
    if not ok:
        warnings.append("The page was written but what read back does not match what was sent.")
    if published:
        warnings.append("Canvas reports the page as published; it was asked for unpublished.")
    url = written.get("html_url") or f"{app.cfg.base_url}/courses/{course_id}/pages/{slug}"
    entry = {**plan, "applied": True, "ok": ok and not published, "url": url, "slug": slug,
             "published": published, "read_back_chars": len(body_back), "warnings": warnings,
             "at": now_iso()}
    save_json(slo_dir(app, course_id) / PUSH_FILE, entry)
    ledger.record(app.course_dir(course_id), AREA,
                  f"{'Replaced' if existing else 'Created'} the unpublished page "
                  f"{REPORT_TITLE} in {label}: {counts.get('coverage', 0)}% of outcomes "
                  f"assessed, {plural(counts.get('gaps', 0), 'gap')} listed",
                  url=url, count=1, kind="slo")
    log(f"REPORT PAGE -> {url} (unpublished). Students cannot see it.")
    for w in warnings:
        log(f"WARNING: {w}")
    return entry


# ------------------------------------------------------------------ local state
def state(app, course_id) -> dict:
    """What the four steps stand at, from disk only. Never calls Canvas."""
    folder = slo_dir(app, course_id)
    resolved = load_json(folder / RESOLVE_FILE)
    items = load_json(folder / ITEMS_FILE)
    slos_data = load_json(folder / SLOS_FILE)
    alignment = load_json(folder / ALIGNMENT_FILE)
    fetched = load_json(folder / "fetch.json")
    pushed = load_json(folder / PUSH_FILE)
    checked = None
    if slos_data and items and alignment:
        checked = validate(slos_data, items, alignment)
    leaves = leaf_outcomes(slos_data) if slos_data else []
    rows = (alignment or {}).get("alignment") or []
    by_id = {str(leaf["id"]): leaf["text"] for leaf in leaves}
    names = {str(i.get("id")): i for i in (items or {}).get("items") or []}
    if pushed:
        step = "done"
    elif checked and checked["ok"]:
        step = "report"
    elif slos_data:
        step = "align"
    elif resolved and not resolved.get("ambiguous"):
        step = "fetch"
    else:
        step = "resolve"
    return {
        "course_id": str(course_id),
        "step": step,
        "resolve": resolved,
        "fetch": fetched,
        "items": (items or {}).get("counts"),
        "outcomes": [{"id": leaf["id"], "text": leaf["text"]} for leaf in leaves],
        "alignment": [{
            "slo": str(r.get("slo")), "text": by_id.get(str(r.get("slo")), ""),
            "verdict": r.get("verdict"), "rationale": r.get("rationale"),
            "suggestion": r.get("suggestion"),
            "evidence": [{"id": str(e), "name": (names.get(str(e)) or {}).get("name", str(e)),
                          "type": (names.get(str(e)) or {}).get("type", ""),
                          "url": (names.get(str(e)) or {}).get("url")}
                         for e in (r.get("evidence") or [])],
        } for r in rows],
        "validate": checked,
        "aligned_at": (alignment or {}).get("at"),
        "cost_usd": (alignment or {}).get("cost_usd"),
        "report": pushed,
        "report_slug": REPORT_SLUG,
        "report_title": REPORT_TITLE,
        "verdicts": list(VERDICTS),
        "has_report_file": (folder / REPORT_HTML).is_file(),
        "needs": [] if pdf_reader_ok() else ["pymupdf"],
    }


def report_html(app, course_id) -> str | None:
    path = slo_dir(app, course_id) / REPORT_HTML
    return path.read_text(encoding="utf-8") if path.is_file() else None


__all__ = ["resolve", "fetch", "slos", "validate", "validate_saved", "align", "report",
           "render", "state", "inventory", "leaf_outcomes", "program_index", "split_code",
           "report_html", "report_sentence", "find_report_page", "slo_dir",
           "REPORT_SLUG", "REPORT_TITLE", "VERDICTS"]
