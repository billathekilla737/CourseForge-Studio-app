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


def _clamp(n: float) -> float:
    return max(0.0, min(100.0, round(float(n), 1)))


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
    return {"kind": "PDFs", "rows": rows, "checked": len(rows),
            "listed": len(listed)}


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
    return {"kind": "Pages", "rows": rows, "checked": len(rows),
            "listed": len(manifest.get("items") or [])}


def _load(path: Path):
    import json
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- the whole
def forecast(app, course_id) -> dict:
    """Every kind the Studio has looked at, before and after, plus what it has
    not looked at -- which is the half of this report that keeps it honest."""
    parts = []
    for fn in (pdfs, html):
        try:
            parts.append(fn(app, course_id))
        except Exception as exc:  # noqa: BLE001
            parts.append({"kind": getattr(fn, "__name__", "?"), "rows": [],
                          "checked": 0, "listed": 0,
                          "error": "%s: %s" % (type(exc).__name__, exc)})

    every = [r for p in parts for r in p["rows"]]
    before = round(sum(r["before"] for r in every) / len(every), 1) if every else None
    after = round(sum(r["after"] for r in every) / len(every), 1) if every else None

    not_looked = [p["kind"] for p in parts if not p["checked"]]
    worst = sorted(every, key=lambda r: r["after"])[:8]

    if before is None:
        headline = ("Nothing in this course has been scanned yet, so there is no "
                    "score to give. Run the PDF or page pass and come back.")
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
    }
