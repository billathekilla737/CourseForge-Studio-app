#!/usr/bin/env python3
"""
check_contrast.py - WCAG 2.x contrast ratio checker. Computes exact ratios instead of
eyeballing colors, and checks against the real thresholds (4.5:1 normal text, 3.0:1
large text/UI boundaries) rather than assuming a color "looks fine."

Usage, single pair:
    python3 check_contrast.py "#2c3a4d" "#ffffff"
    python3 check_contrast.py "#2c3a4d" "#ffffff" --need 4.5

Usage, a file of pairs (one "fg bg [need] [label...]" per line, '#' comments ok):
    python3 check_contrast.py --pairs pairs.txt

pairs.txt example:
    # fg        bg        need   label
    #2c3a4d     #ffffff   4.5    body text on white
    #ffffff     #061E3F   3.0    hero title (30px) on navy
"""
import argparse
import sys


def luminance(hexcolor):
    h = hexcolor.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    rgb = [(c / 12.92) if c <= 0.04045 else (((c + 0.055) / 1.055) ** 2.4) for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check_one(fg, bg, need, label=None):
    r = ratio(fg, bg)
    ok = r >= need
    tag = "PASS" if ok else "FAIL"
    lbl = (" " + label) if label else ""
    print("  %-4s %6.2f:1  (need %.1f)  %s on %s%s" % (tag, r, need, fg, bg, lbl))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fg", nargs="?", help="foreground/text hex color")
    ap.add_argument("bg", nargs="?", help="background hex color")
    ap.add_argument("--need", type=float, default=4.5, help="minimum ratio (default 4.5, WCAG AA normal text)")
    ap.add_argument("--pairs", help="file of 'fg bg [need] [label...]' lines to check in bulk")
    args = ap.parse_args()

    results = []
    if args.pairs:
        with open(args.pairs, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 3)
                fg, bg = parts[0], parts[1]
                need = float(parts[2]) if len(parts) > 2 else 4.5
                label = parts[3] if len(parts) > 3 else None
                results.append(check_one(fg, bg, need, label))
    elif args.fg and args.bg:
        results.append(check_one(args.fg, args.bg, args.need))
    else:
        ap.error("pass fg + bg, or --pairs a-file")

    fails = results.count(False)
    print()
    print("RESULT: %d checked, %d failed" % (len(results), fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
