"""Every manifest slot has a body that belongs to it. Run before any push.

Ported from Verify-Slots.ps1, the one check that pipeline called
non-negotiable: a body can be perfectly styled and still belong to a different
lesson. Each page's hero <h2> is compared with the title of the slot it was
assigned to. Beyond that, each body must exist, be non-empty, carry exactly one
<h2>, and be pure ASCII (a raw emoji in a body is a Canvas 500). Assignments,
discussions and quizzes get the existence and ASCII checks and a check_quiz
pass on the questions; module items must point at slots that exist.

    result = verify_slots.verify(manifest, root)
    result["ok"]      # True = safe to push
    result["rows"]    # one per slot: {kind, key, title, file, state, problems}

    python -m courseforge.content.verify_slots manifest.json [--root folder]
Exit code 0 = safe to push, 2 = something is wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import check_quiz, check_style, manifest as mf


def norm(s: str) -> str:
    s = (s or "").replace("&amp;", "&").replace("&mdash;", "-").replace("&ndash;", "-")
    s = s.replace("&ldquo;", '"').replace("&rdquo;", '"')
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[^a-zA-Z0-9]", "", s).lower()


def hero_of(html: str) -> str:
    m = re.search(r"<h2[^>]*>(.*?)</h2>", html or "", re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def title_matches(slot_title: str, hero: str) -> bool:
    nh, nt = norm(hero), norm(slot_title)
    if not nh:
        return False
    if nh == nt or (nt and nt in nh) or (nh in nt):
        return True
    # The syllabus is titled "Course Syllabus" while its hero keeps the long title.
    return "syllabus" in nt and "syllabus" in nh


def _body_checks(entry: dict, root: Path, need_h2: bool, title: str) -> tuple[str, list[str], list[str]]:
    """-> (body, problems, warnings)."""
    problems: list[str] = []
    warnings: list[str] = []
    if not mf.has_body(entry):
        return "", ["no file or html"], warnings
    path = mf.resolve_file(entry, root)
    if entry.get("html") is None:
        if path is None or not path.is_file():
            return "", [f"missing file: {path}"], warnings
    try:
        body = mf.body_of(entry, root)
    except (OSError, UnicodeDecodeError) as exc:
        return "", [f"cannot read {path}: {exc}"], warnings
    if not re.sub(r"<[^>]+>|&nbsp;|\s", "", body):
        problems.append("body is empty")
        return body, problems, warnings
    h2s = re.findall(r"<h2\b", body, re.I)
    if need_h2:
        if len(h2s) != 1:
            problems.append(f"{len(h2s)} <h2> found (expected exactly 1)")
        elif not title_matches(title, hero_of(body)):
            problems.append(f"hero '{hero_of(body)[:60]}' does not match the slot title '{title}'")
    elif len(h2s) > 1:
        warnings.append(f"{len(h2s)} <h2> found")
    nonascii = sorted(set(c for c in body if ord(c) >= 128))
    if nonascii:
        problems.append("non-ASCII characters present: " + "".join(nonascii[:8]) + " (entity-encode them)")
    return body, problems, warnings


def verify(manifest: dict, root: Path, palette: set[str] | None = None,
           style: bool = True) -> dict:
    rows: list[dict] = []
    mode = mf.detect_mode(manifest)
    root = Path(root)
    structural = mf.validate(manifest, root)

    def row(kind, key, title, entry, problems, warnings, extra=None):
        r = {"kind": kind, "key": key, "title": title, "file": entry.get("file") or ("inline" if entry.get("html") is not None else ""),
             "state": "fail" if problems else ("warn" if warnings else "ok"),
             "problems": problems, "warnings": warnings}
        if extra:
            r.update(extra)
        rows.append(r)

    for p in [e for e in manifest.get("pages") or [] if isinstance(e, dict)]:
        title = p.get("title") or ""
        key = mf.page_key(p)
        body, problems, warnings = _body_checks(p, root, need_h2=True, title=title)
        extra = {}
        if style and body and not problems:
            rep = check_style.check(body, palette=palette)
            extra["style"] = {"ok": rep["ok"], "failed": rep["failed"], "warned": rep["warned"], "chips": rep["chips"]}
            if not rep["ok"]:
                problems = problems + [f"style: {f}" for f in rep["failed"][:4]]
        row("page", key, title, p, problems, warnings, extra)

    if mode == "project":
        if manifest.get("syllabus_file") or manifest.get("syllabus_html") is not None:
            entry = {"file": manifest.get("syllabus_file"), "html": manifest.get("syllabus_html")}
            body, problems, warnings = _body_checks(entry, root, need_h2=False, title="Syllabus")
            row("syllabus", "syllabus", "Syllabus", entry, problems, warnings)
        for a in [e for e in manifest.get("assignments") or [] if isinstance(e, dict)]:
            body, problems, warnings = _body_checks(a, root, need_h2=False, title=a.get("name") or "")
            row("assignment", str(a.get("key") or ""), a.get("name") or "", a, problems, warnings,
                {"points": a.get("points")})
        for d in [e for e in manifest.get("discussions") or [] if isinstance(e, dict)]:
            body, problems, warnings = _body_checks(d, root, need_h2=False, title=d.get("title") or "")
            row("discussion", str(d.get("key") or ""), d.get("title") or "", d, problems, warnings,
                {"points": d.get("points")})
        for q in [e for e in manifest.get("quizzes") or [] if isinstance(e, dict)]:
            problems: list[str] = []
            warnings: list[str] = []
            if mf.has_body(q):
                _, problems, warnings = _body_checks(q, root, need_h2=False, title=q.get("title") or "")
            rep = check_quiz.check(list(q.get("questions") or []))
            problems += [f"questions: {f}" for f in rep["failed"][:6]]
            warnings += rep["warned"][:4]
            row("quiz", str(q.get("key") or ""), q.get("title") or "", q, problems, warnings,
                {"questions": rep["count"], "points": rep["points"]})

    failures = [r for r in rows if r["state"] == "fail"]
    return {"ok": not failures and not structural, "mode": mode, "rows": rows,
            "structural": structural, "total": len(rows), "failed": len(failures),
            "summary": (f"{len(rows) - len(failures)}/{len(rows)} slots verified"
                        + (f", {len(failures)} failing" if failures else "")
                        + (f", {len(structural)} manifest problem(s)" if structural else ""))}


def print_report(result: dict, out=sys.stdout) -> None:
    for s in result["structural"]:
        print("  MANIFEST  " + s, file=out)
    for r in result["rows"]:
        tag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[r["state"]]
        print(f"  {tag}  {r['kind']:<11} {r['title'][:50]}", file=out)
        for p in r["problems"]:
            print("            - " + p, file=out)
        for w in r["warnings"]:
            print("            ~ " + w, file=out)
    print(file=out)
    print(result["summary"], file=out)
    if not result["ok"]:
        print("DO NOT PUSH until these are resolved.", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check every manifest slot before pushing.")
    ap.add_argument("manifest")
    ap.add_argument("--root", default=None, help="folder that relative file paths resolve against")
    ap.add_argument("--no-style", action="store_true", help="skip check_style on page bodies")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    manifest = mf.load(args.manifest)
    root = mf.resolve_root(manifest, manifest_path=Path(args.manifest),
                           explicit=Path(args.root) if args.root else None)
    result = verify(manifest, root, palette=check_style.brand_palette(), style=not args.no_style)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
