"""repair-misplaced-alt.py - one-off repair for the pre-1.1.8 alt-index bug.

WHAT WENT WRONG
Before v1.1.8, three parts of the alt pipeline disagreed about what a figure's
index counted (alt-LESS figures in one place, ALL figures in another). On a PDF
where some figures already carried the author's own alt text, a model-written
description landed on the WRONG figure - overwriting the author's words - while
the figure that actually needed alt kept the "Graphic in this document"
placeholder.

WHO IS AFFECTED
Only files that (a) mix already-described and undescribed figures AND (b) were
in a describe run. This script finds them by signature: a placeholder sitting
at an earlier position than a written description, which cannot happen when the
mapping is correct.

THE FIX
original.pdf is still on disk for every affected file, so the author's original
alt text is recoverable. Deleting fixed.pdf + result.json makes the next
"Back up & Fix" rebuild that file from its untouched original; the v1.1.8
pipeline then puts descriptions on the right figures and refuses to overwrite
authored alt. Nothing in Canvas changes until you press Upload.

USAGE
    python repair-misplaced-alt.py                 # report only (default)
    python repair-misplaced-alt.py --apply         # reset the affected files

Then, per affected course, in CourseForge PDF Fixer:
    Back up & Fix  ->  Describe images  ->  Upload to Canvas

Only the reset files are reprocessed; everything else is left cached.
"""
import argparse
import glob
import json
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "skill", "scripts")
if not os.path.isdir(SCRIPTS):
    SCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                           "courseforge", "scripts")
sys.path.insert(0, SCRIPTS)

import pdf_fastlane as E                                    # noqa: E402
from pdf_fastlane import pikepdf                            # noqa: E402

WORKROOT = os.path.join(os.path.expanduser("~"), "Documents", "CourseForge-PDF")


def written_descriptions(workdir):
    ap = os.path.join(workdir, "alt.json")
    if not os.path.isfile(ap):
        return set()
    try:
        with open(ap, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return set()
    return {v.strip() for v in data.values()
            if isinstance(v, str) and v.strip()}


def affected_files(workdir):
    """[(dir, display_name, n_figures, n_misplaced)] for this course."""
    written = written_descriptions(workdir)
    if not written:
        return []
    out = []
    for fx in sorted(glob.glob(os.path.join(workdir, "*", "fixed.pdf"))):
        sub = os.path.dirname(fx)
        try:
            with pikepdf.open(fx) as pdf:
                alts = [E._alt_text(n).strip()
                        for _o, n, _p in E.walk_figures(pdf)]
        except Exception:
            continue
        if not alts:
            continue
        last_desc = max([i for i, a in enumerate(alts) if a in written],
                        default=-1)
        if last_desc < 0:
            continue
        misplaced = [i for i, a in enumerate(alts)
                     if a == E.PLACEHOLDER_ALT and i < last_desc]
        if not misplaced:
            continue
        name = os.path.basename(sub)
        mp = os.path.join(sub, "file.json")
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8-sig") as f:
                    name = json.load(f)["display_name"]
            except Exception:
                pass
        out.append((sub, name, len(alts), len(misplaced)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually delete fixed.pdf + result.json for the "
                         "affected files (originals are never touched)")
    ap.add_argument("--workroot", default=WORKROOT)
    args = ap.parse_args()

    if not os.path.isdir(args.workroot):
        print("No work folder at %s" % args.workroot)
        return 1

    total = reset = 0
    no_original = []
    for course in sorted(os.listdir(args.workroot)):
        wd = os.path.join(args.workroot, course, "pdf-fastlane")
        if not os.path.isdir(wd):
            continue
        aff = affected_files(wd)
        if not aff:
            continue
        print()
        print("course %s - %d file(s) with misplaced descriptions:"
              % (course, len(aff)))
        for sub, name, nfig, nbad in aff:
            total += 1
            orig = os.path.join(sub, "original.pdf")
            if not os.path.isfile(orig):
                no_original.append((course, name))
                print("   !! %-56s %2d figures, %d misplaced - NO ORIGINAL, "
                      "cannot rebuild" % (name[:56], nfig, nbad))
                continue
            print("      %-56s %2d figures, %d misplaced"
                  % (name[:56], nfig, nbad))
            if args.apply:
                for junk in ("fixed.pdf", "result.json", "fixed.pdf.prealt"):
                    p = os.path.join(sub, junk)
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except OSError as e:
                            print("         could not remove %s (%s)" % (junk, e))
                reset += 1

    print()
    if not total:
        print("Nothing to repair - no file shows the misplaced-alt signature.")
        return 0
    print("%d affected file(s) across the courses above." % total)
    if no_original:
        print("%d of them have no original.pdf and need a fresh Back up & Fix "
              "from Canvas first." % len(no_original))
    if args.apply:
        print("Reset %d file(s). In CourseForge PDF Fixer, per course:" % reset)
        print("   1. Back up & Fix PDFs   (rebuilds just these from the originals)")
        print("   2. Describe images      (correct figures this time)")
        print("   3. Upload to Canvas     (nothing changes until you do this)")
    else:
        print("Report only - nothing was changed. Re-run with --apply to reset "
              "these files so the next Back up & Fix rebuilds them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
