"""WCAG 2.x contrast ratio checker (ported from the old toolkit's
check_contrast.py). Computes exact ratios instead of eyeballing colors, against
the real thresholds: 4.5:1 normal text, 3.0:1 large text and UI boundaries.

    ratio("#2c3a4d", "#ffffff") -> 10.6...
    check_pair(fg, bg, need=4.5, label=None) -> {"fg", "bg", "ratio", "need", "ok", "label"}
    check_pairs(lines) -> list of results, one per "fg bg [need] [label...]" line
    brand_pairs() -> the pairs the restyler's palette relies on

CLI:
    python -m courseforge.a11y.contrast "#2c3a4d" "#ffffff" [--need 4.5]
    python -m courseforge.a11y.contrast --pairs pairs.txt
    python -m courseforge.a11y.contrast --brand
"""
from __future__ import annotations

import argparse
import sys

from . import restyle


def _expand(hexcolor: str) -> str:
    h = hexcolor.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError("not a hex color: %r" % hexcolor)
    return h


def luminance(hexcolor: str) -> float:
    h = _expand(hexcolor)
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    rgb = [(c / 12.92) if c <= 0.04045 else (((c + 0.055) / 1.055) ** 2.4) for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check_pair(fg: str, bg: str, need: float = 4.5, label: str | None = None) -> dict:
    r = ratio(fg, bg)
    return {"fg": fg, "bg": bg, "ratio": round(r, 2), "need": float(need),
            "ok": r >= float(need), "label": label or ""}


def check_pairs(lines) -> list[dict]:
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") and not line[1:2].isalnum():
            continue
        if line.startswith("#") and len(line.split()) == 1:
            continue
        parts = line.split(None, 3)
        if len(parts) < 2:
            continue
        fg, bg = parts[0], parts[1]
        need = float(parts[2]) if len(parts) > 2 else 4.5
        label = parts[3] if len(parts) > 3 else None
        out.append(check_pair(fg, bg, need, label))
    return out


def brand_pairs() -> list[dict]:
    c = restyle.C
    pairs = [
        (c["body_text"], c["card_fill"], 4.5, "body text on a card"),
        (c["body_text"], c["page_bg"], 4.5, "body text on the page"),
        (c["muted_text"], c["card_fill"], 4.5, "muted text on a card"),
        (c["navy"], c["card_fill"], 4.5, "navy headings on white"),
        (c["on_navy_text"], c["navy"], 4.5, "white text on navy"),
        (c["on_navy_muted"], c["navy"], 4.5, "muted text on navy"),
        (c["gold"], c["navy"], 3.0, "gold eyebrow (bold, large) on navy"),
        (c["red"], c["alert_fill"], 4.5, "alert heading on the alert fill"),
        (c["body_text"], c["goal_fill"], 4.5, "body text on the goal fill"),
        (c["body_text"], c["callout_fill"], 4.5, "body text on the callout fill"),
    ]
    return [check_pair(fg, bg, need, label) for fg, bg, need, label in pairs]


def _print(results: list[dict]) -> int:
    for r in results:
        print("  %-4s %6.2f:1  (need %.1f)  %s on %s %s" % (
            "PASS" if r["ok"] else "FAIL", r["ratio"], r["need"], r["fg"], r["bg"], r["label"]))
    fails = sum(1 for r in results if not r["ok"])
    print()
    print("RESULT: %d checked, %d failed" % (len(results), fails))
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fg", nargs="?", help="foreground/text hex color")
    ap.add_argument("bg", nargs="?", help="background hex color")
    ap.add_argument("--need", type=float, default=4.5, help="minimum ratio (default 4.5, WCAG AA normal text)")
    ap.add_argument("--pairs", help="file of 'fg bg [need] [label...]' lines to check in bulk")
    ap.add_argument("--brand", action="store_true", help="check the pairs brand.json relies on")
    args = ap.parse_args(argv)
    if args.pairs:
        with open(args.pairs, encoding="utf-8") as f:
            results = check_pairs(f.readlines())
    elif args.brand:
        results = brand_pairs()
    elif args.fg and args.bg:
        results = [check_pair(args.fg, args.bg, args.need)]
    else:
        ap.error("pass fg + bg, or --pairs a-file, or --brand")
        return 2
    return _print(results)


if __name__ == "__main__":
    sys.exit(main())
