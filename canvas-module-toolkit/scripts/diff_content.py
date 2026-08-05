#!/usr/bin/env python3
"""
diff_content.py - word-level content-preservation check between an original HTML/text
body and a restyled/edited version. Answers one question cheaply: did the rewrite
accidentally drop instructional content, or only change markup/punctuation/wording?

This is deliberately NOT a line-diff (line diffs on reformatted HTML are almost 100%
noise). It compares word multisets, so re-wrapping, re-indenting, and swapping
"Label - value" for "Label: value" show up as zero-impact, while an actually deleted
sentence shows up as a cluster of lost words you can eyeball in one screen.

Usage:
    python3 diff_content.py original.html restyled.html
    python3 diff_content.py original.html restyled.html --top 40

Exit code is always 0 (this is a report, not a pass/fail gate) - read the LOST section
yourself; a few connector words (the, and, a, is) disappearing is normal restructuring,
whole nouns/numbers disappearing is not.
"""
import argparse
import html
import re
import sys
from collections import Counter


def words(path):
    h = open(path, encoding="utf-8").read()
    h = re.sub(r"<(script|style)\b.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h)
    return Counter(re.findall(r"[a-z0-9]+", h.lower()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("restyled")
    ap.add_argument("--top", type=int, default=30, help="max words to list per section")
    args = ap.parse_args()

    a = words(args.original)
    b = words(args.restyled)
    lost = a - b
    gained = b - a

    print("original: %d words   restyled: %d words" % (sum(a.values()), sum(b.values())))
    print()
    print("LOST (present in original, missing from restyled) - top %d:" % args.top)
    if not lost:
        print("  (none)")
    for w, c in sorted(lost.items(), key=lambda x: -x[1])[: args.top]:
        print("  -%-3d %s" % (c, w))

    print()
    print("GAINED (new in restyled) - top %d:" % args.top)
    for w, c in sorted(gained.items(), key=lambda x: -x[1])[: args.top]:
        print("  +%-3d %s" % (c, w))

    print()
    lost_total = sum(lost.values())
    print("Read this yourself: a handful of connector words (the/and/a/is/or) lost is normal")
    print("restructuring. Nouns, numbers, names, or whole phrases lost means content is gone.")
    print("(%d total word-instances lost, %d gained; sizes alone don't tell you which.)" % (lost_total, sum(gained.values())))


if __name__ == "__main__":
    main()
