"""Word-level content-preservation check between two HTML bodies (library and CLI).

Ported from the old toolkit's diff_content.py. Not a line diff: reformatted HTML
makes those pure noise. It compares word multisets, so re-wrapping and swapping
"Label - value" for "Label: value" cost nothing, while a deleted sentence shows
up as a cluster of lost words.

    result = diff_content.diff(original_html, edited_html)
    result["lost"], result["gained"], result["lost_total"]

    python -m courseforge.content.diff_content original.html restyled.html [--top 40]
"""
from __future__ import annotations

import argparse
import html as htmlmod
import re
import sys
from collections import Counter
from pathlib import Path


def words(text: str) -> Counter:
    h = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = htmlmod.unescape(h)
    return Counter(re.findall(r"[a-z0-9]+", h.lower()))


def diff(original: str, edited: str, top: int = 30) -> dict:
    a, b = words(original), words(edited)
    lost, gained = a - b, b - a
    rank = lambda c: sorted(c.items(), key=lambda x: (-x[1], x[0]))[:top]  # noqa: E731
    return {
        "original_words": sum(a.values()),
        "edited_words": sum(b.values()),
        "lost": [{"word": w, "count": n} for w, n in rank(lost)],
        "gained": [{"word": w, "count": n} for w, n in rank(gained)],
        "lost_total": sum(lost.values()),
        "gained_total": sum(gained.values()),
    }


def print_report(result: dict, out=sys.stdout) -> None:
    print(f"original: {result['original_words']} words   edited: {result['edited_words']} words", file=out)
    print("\nLOST (in the original, missing from the edit):", file=out)
    if not result["lost"]:
        print("  (none)", file=out)
    for row in result["lost"]:
        print(f"  -{row['count']:<3d} {row['word']}", file=out)
    print("\nGAINED (new in the edit):", file=out)
    if not result["gained"]:
        print("  (none)", file=out)
    for row in result["gained"]:
        print(f"  +{row['count']:<3d} {row['word']}", file=out)
    print(f"\n{result['lost_total']} word instances lost, {result['gained_total']} gained. "
          "A few connector words lost is restructuring; nouns, numbers or names lost is content gone.",
          file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Word-level content check between two HTML bodies.")
    ap.add_argument("original")
    ap.add_argument("edited")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args(argv)
    result = diff(Path(args.original).read_text(encoding="utf-8"),
                  Path(args.edited).read_text(encoding="utf-8"), args.top)
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
