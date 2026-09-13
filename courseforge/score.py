"""An accessibility score for a course, before and after the Studio touched it.

The point of this is a sentence somebody can say to a dean. "I fixed the PDFs"
is a description of effort; "this course went from 61 to 94" is a result, and
it is the second one that gets a term of remediation funded again.

## What this number is, and what it is not

**It is not your Ally score.** Ally's algorithm is Anthology's and it is not
published; anything here claiming to be it would be a guess that a dean could
check against the real dashboard in thirty seconds, and being caught out on
that costs more than the report is worth. What this measures is the same set of
defects Ally penalises -- a scanned PDF with no text layer, a picture with no
alt text, a page with no heading structure, a table with no header row -- with
the weights written down below and printed on the report.

Expect it to move in the same direction as Ally and by a similar amount. Do not
expect the two numbers to match.

**Before is not a guess either.** The Studio keeps every original, so "before"
is measured from the file as it arrived, not modelled from what was wrong with
it. That is the part of this worth trusting most.

**What has not been looked at is not scored.** A course where nobody has run
the PDF pass has no PDF score, and the report says so rather than averaging
over an assumption. This is the difference between "94 out of what we checked"
and "94", and only the first one is true.

**"Not scanned" is three different sentences and the report must say which.**
A course can be unscored because nothing has ever asked Canvas what is in it,
because the files are listed and nobody has opened them, or because there are
genuinely no files of that kind in the course. The first two are work waiting;
the third is a course with nothing to fix, and printing it as a gap sends
somebody hunting for PDFs that do not exist. `state` on each part carries the
difference, and `needs_scan` on the whole says which courses a scan would
actually change.
"""
from __future__ import annotations

from pathlib import Path

# What each defect costs, out of 100, per file. Chosen to line up with the
# order of severity Ally uses rather than its exact arithmetic: a scanned image
# with no text at all is unusable by a screen reader and scores near zero,
# while a missing table header is a real but survivable problem.
WEIGHTS = {
    # PDFs
    "pdf_scanned": 85,          # no text layer: unreadable, not merely awkward
    "pdf_untagged": 45,         # text, but no structure to navigate by
    "pdf_no_ua": 20,            # tagged, still fails PDF/UA-1
    "pdf_figure_no_alt": 8,     # each, capped below
    "pdf_no_title": 5,
    # HTML bodies
    "html_img_no_alt": 12,      # each, capped
    "html_alt_filename": 8,
    "html_no_heading": 15,
    "html_skipped_heading": 8,
    "html_table_no_header": 10,
    "html_empty_link": 6,
    "html_script": 6,
    # Office documents
    "office_no_alt": 12,
    "office_no_title": 8,
    "office_table_no_header": 10,
}
# One defect repeated twenty times in one file is one problem to fix, not
# twenty, so a repeated deduction stops counting after this.
REPEAT_CAP = 3


# The four states a kind of file can be in for one course. Only two of them
# are work waiting; the difference is the whole point of printing them.
SCORED = "scored"        # we looked, here is a number
WAITING = "waiting"      # listed from Canvas, nobody has opened them
UNLISTED = "unlisted"    # nothing has ever asked Canvas what is in this course
EMPTY = "empty"          # asked, and the course has none of this kind


def _clamp(n: float) -> float:
    return max(0.0, min(100.0, round(float(n), 1)))


def _noun(kind: str, n: int) -> str:
    """'10 PowerPoint files', not '10 PowerPoint'."""
    if kind in ("PDFs", "Pages"):
        return "%d %s" % (n, kind[:-1] if n == 1 else kind)
    return "%d %s file%s" % (n, kind, "" if n == 1 else "s")


def _part(kind, rows, listed, has_listing) -> dict:
    """One kind of file in one course, with an honest word for its state."""
    if rows:
        state, note = SCORED, ""
    elif not has_listing:
        state = UNLISTED
        note = "never looked at"
    elif not listed:
        state = EMPTY
        note = "none in this course"
    else:
        state = WAITING
        note = "%s, not scanned yet" % _noun(kind, listed)
    return {"kind": kind, "rows": rows, "checked": len(rows), "listed": listed,
            "state": state, "note": note}


def score_defects(defects: list[str]) -> float:
    """100 minus what the defects cost, with repeats capped."""
    seen: dict[str, int] = {}
    total = 0.0
    for name in defects:
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > REPEAT_CAP:
            continue
        total += WEIGHTS.get(name, 0)
    return _clamp(100.0 - total)


# --------------------------------------------------------------------- PDFs
def _was_untagged(result: dict) -> bool:
    klass = str((result or {}).get("class_before") or "")
    return "scan" in klass or "untagged" in klass


def _pdf_defects(result: dict, compliant: bool | None, figures_missing_alt: int) -> list[str]:
    klass = str((result or {}).get("class_before") or "")
    out: list[str] = []
    if "scan" in klass:
        out.append("pdf_scanned")
    elif "untagged" in klass:
        out.append("pdf_untagged")
    if compliant is False:
        out.append("pdf_no_ua")
    out += ["pdf_figure_no_alt"] * max(0, int(figures_missing_alt))
    return out


def _ua_is_comparable(result: dict) -> bool:
    """Whether a PDF/UA verdict may be counted at all for this file.

    veraPDF is only ever run on the fixed copy, so for a file that arrived
    already tagged there is no before reading to compare against. Counting the
    after verdict anyway means charging the score for something the check
    discovered rather than caused, and a course of already-tagged PDFs then
    scores *worse* after being checked -- which is what this did, and which is
    the one result that would get somebody laughed out of a dean's office.

    A scanned or untagged PDF is different: PDF/UA-1 requires a tag tree, so
    the original certainly failed. That is a fact, not an assumption, and both
    sides can carry it.
    """
    return _was_untagged(result)


def pdfs(app, course_id) -> dict:
    """Before and after for every PDF the Studio has actually looked at."""
    from .pdf import course_pdfs as core
    wd = core.workdir(app, course_id)
    has_listing = (wd / "files.json").is_file()
    state_files = core.read_json(wd / "files.json", {}) or {}
    listed = state_files.get("files") or []
    validation = core.read_json(wd / "validation.json", {}) or {}
    compliant_dirs = {str(r.get("dir")): bool(r.get("compliant"))
                      for r in (validation.get("per_file") or [])}
    per_dir, _todo = core._alt_map(wd)

    rows = []
    for meta in listed:
        fid = str(meta.get("id"))
        sub = wd / fid
        result = core.read_json(sub / "result.json", None)
        if not result:
            continue                       # never processed: not scored, not assumed
        waiting = int(per_dir.get(fid, 0))
        # The UA verdict only counts where both sides can carry it: an untagged
        # original certainly failed PDF/UA-1, so the before score owns that too.
        comparable = _ua_is_comparable(result)
        before = score_defects(_pdf_defects(
            result, False if comparable else None, waiting))
        fixed = (sub / "fixed.pdf").is_file()
        after = before
        if fixed:
            # A fixed PDF is tagged by definition, so the two big deductions are
            # gone. What can survive is a veraPDF failure and figures still on a
            # placeholder description.
            after_defects = []
            if comparable and compliant_dirs.get(fid) is False:
                after_defects.append("pdf_no_ua")
            after_defects += ["pdf_figure_no_alt"] * waiting
            after = score_defects(after_defects)
        rows.append({
            "id": fid, "name": meta.get("display_name") or fid,
            "was": str(result.get("class_before") or "unknown"),
            "before": before, "after": after, "fixed": fixed,
            "compliant": compliant_dirs.get(fid),
            "ua_counted": comparable,
            "figures_waiting": waiting,
        })
    return _part("PDFs", rows, len(listed), has_listing)


# ---------------------------------------------------- PowerPoint and Word
def _office_defects(undescribed: int, untitled: int, headerless: int) -> list[str]:
    return (["office_no_alt"] * max(0, undescribed)
            + ["office_no_title"] * max(0, untitled)
            + ["office_table_no_header"] * max(0, headerless))


def office(app, course_id, kind_id: str) -> dict:
    """Before and after for the course's PowerPoints or Word documents.

    These were missing from the score, and their absence was not a rounding
    error. A course of eleven tidy PDFs and twenty-five decks holding six
    hundred undescribed pictures scored 97.8, because only the PDFs were
    counted -- a number that would survive exactly as long as it took a dean to
    open one of the decks.
    """
    from .docs import gateway
    kind = gateway.KINDS[kind_id]
    listed = gateway.listed_files(app, course_id, kind) or {}
    has_listing = (gateway.kind_dir(app, course_id, kind) / "files.json").is_file()
    names = listed.get("files") or []

    rows = []
    for item in gateway.items(app, course_id, kind):
        if not item.report:
            continue                       # never scanned: not scored, not assumed
        summary = kind.adapter.summary(item.report, item.fixes) or {}
        fixes = item.fixes or {}
        # Before is the file as it arrived: every picture that needed a
        # description needed one, whether or not somebody has since written it.
        undescribed = int(summary.get("alt_todo") or 0) + int(summary.get("alt_done") or 0)
        untitled = int(summary.get("untitled") or 0)
        headerless = int(summary.get("tables_without_header") or 0)
        before = score_defects(_office_defects(undescribed, untitled, headerless))

        fixed = item.fixed.is_file()
        after = before
        if fixed:
            titled = sum(1 for v in (fixes.get("titles") or {}).values() if (v or "").strip())
            after = score_defects(_office_defects(
                int(summary.get("alt_todo") or 0),
                max(0, untitled - titled),
                0 if fixes.get("table_headers", True) else headerless))
        rows.append({
            "id": item.id, "name": item.meta.get("display_name") or item.id,
            "was": "%d picture%s without a description"
                   % (undescribed, "" if undescribed == 1 else "s"),
            "before": before, "after": after, "fixed": fixed,
            "figures_waiting": int(summary.get("alt_todo") or 0),
        })
    return _part(kind.label, rows, len(names), has_listing)


# -------------------------------------------------------------------- HTML
_HTML_MAP = [
    ("img missing alt", "html_img_no_alt"),
    ("alt is a filename", "html_alt_filename"),
    ("no semantic heading", "html_no_heading"),
    ("skipped heading level", "html_skipped_heading"),
    ("table without", "html_table_no_header"),
    ("empty link", "html_empty_link"),
    ("script element", "html_script"),
    ("inline event handler", "html_script"),
    ("javascript: link", "html_script"),
]


def _html_defects(issues: list) -> list[str]:
    out = []
    for issue in issues or []:
        text = str(issue)
        for needle, name in _HTML_MAP:
            if needle in text:
                out.append(name)
                break
    return out


def html(app, course_id) -> dict:
    """Before and after for the course's pages, from the a11y scan on disk."""
    from .a11y import workdir as wdmod
    wd = wdmod.workdir(app.course_dir(course_id))
    has_listing = (wd / "manifest.json").is_file()
    manifest = _load(wd / "manifest.json")
    report = _load(wd / "verify-report.json")
    verified = {str(r.get("key")): r for r in (report.get("items") or [])} if report else {}

    rows = []
    for item in (manifest.get("items") or []):
        issues = item.get("issues")
        if issues is None:
            continue                       # never scanned
        before = score_defects(_html_defects(issues))
        done = verified.get(str(item.get("key")))
        # A restyle fixes structure, not missing alt text: a picture with no
        # description still has none afterwards, and saying otherwise would be
        # the report flattering the tool.
        kept = [d for d in _html_defects(issues)
                if d in ("html_img_no_alt", "html_alt_filename")]
        after = score_defects(kept) if done else before
        rows.append({
            "id": str(item.get("key")), "name": item.get("title") or item.get("key"),
            "kind": item.get("kind") or "page",
            "before": before, "after": after, "fixed": bool(done),
        })
    return _part("Pages", rows, len(manifest.get("items") or []), has_listing)


def _load(path: Path):
    import json
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- the whole
# Which kind on the report maps to which kind the file scan understands, so
# the page can offer to run exactly the passes that are missing.
_KIND_ID = {"PDFs": "pdf", "PowerPoint": "pptx", "Word": "docx"}



def forecast(app, course_id) -> dict:
    """Every kind the Studio has looked at, before and after, plus what it has
    not looked at -- which is the half of this report that keeps it honest."""
    jobs = [("PDFs", pdfs), ("PowerPoint", lambda a, c: office(a, c, "pptx")),
            ("Word", lambda a, c: office(a, c, "docx")), ("Pages", html)]
    parts = []
    for label, fn in jobs:
        try:
            parts.append(fn(app, course_id))
        except Exception as exc:  # noqa: BLE001
            parts.append({"kind": label, "rows": [], "checked": 0, "listed": 0,
                          "state": UNLISTED, "note": "could not be read",
                          "error": "%s: %s" % (type(exc).__name__, exc)})

    every = [r for p in parts for r in p["rows"]]
    before = round(sum(r["before"] for r in every) / len(every), 1) if every else None
    after = round(sum(r["after"] for r in every) / len(every), 1) if every else None

    # A kind with nothing of it in the course is not a gap, and listing it as
    # one sends somebody looking for PDFs that were never there.
    waiting = [p for p in parts if p.get("state") in (WAITING, UNLISTED)]
    not_looked = [p["kind"] for p in waiting]
    none_here = [p["kind"] for p in parts if p.get("state") == EMPTY]
    # Pages come from a different pass, so the file scan would not touch them.
    scannable = [p for p in waiting if p["kind"] != "Pages"]
    worst = sorted(every, key=lambda r: r["after"])[:8]

    if before is None and not any(p.get("listed") for p in parts):
        headline = ("Nothing in this course has been looked at yet -- not even its "
                    "file list has been read, so there is nothing here to score.")
    elif before is None:
        headline = ("%s are listed in this course and none of them has been "
                    "scanned yet, so there is no score to give. Scan them and "
                    "come back."
                    % ", ".join(_noun(p["kind"], p["listed"])
                                for p in scannable if p["listed"]))
    elif after > before:
        headline = ("By the measures Ally uses, this course scores %.0f where it "
                    "scored %.0f before the Studio touched it, across %d file%s."
                    % (after, before, len(every), "" if len(every) == 1 else "s"))
    else:
        headline = ("Scored %.0f across %d file%s. Nothing has been fixed here "
                    "yet, so before and after are the same number."
                    % (after, len(every), "" if len(every) == 1 else "s"))

    return {
        "course_id": str(course_id),
        "before": before, "after": after,
        "gain": round(after - before, 1) if before is not None else None,
        "files": len(every),
        "parts": parts,
        "worst": worst,
        "not_checked": not_looked,
        "none_here": none_here,
        # What a scan would actually change here. A course with nothing waiting
        # is finished, not neglected, and the page must not offer to rescan it.
        "needs_scan": bool(scannable),
        "scan_kinds": sorted({_KIND_ID[p["kind"]] for p in scannable
                              if p["kind"] in _KIND_ID}),
        "waiting": [{"kind": p["kind"], "listed": p["listed"],
                     "state": p["state"], "note": p["note"]} for p in waiting],
        "headline": headline,
        "method": ("The Studio's own score, not Anthology's. It counts the same "
                   "defects Ally penalises -- a scanned PDF with no text layer, a "
                   "picture with no alt text, a page with no headings, a table "
                   "with no header row -- using the weights in courseforge/"
                   "score.py. Before is measured from the originals kept on this "
                   "computer, not estimated. Expect it to move with your Ally "
                   "score; do not expect it to match."),
        "caveat": (("Not counted: %s, because nothing here has scanned %s yet. "
                    "The score is out of what was checked."
                    % (", ".join(not_looked), "them" if len(not_looked) > 1 else "it"))
                   if not_looked else ""),
        "method_note": ("It counts PDFs, PowerPoint decks, Word documents and "
                        "pages -- whichever of those this computer has "
                        "actually looked at."),
    }
