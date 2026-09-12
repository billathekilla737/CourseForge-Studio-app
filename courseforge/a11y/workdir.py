"""Where the HTML remediation keeps its work for one course, and the small
JSON files it reads and writes there.

    data/<cid>/a11y/
      manifest.json         what was fetched, how it was transformed, when it
                            was verified and pushed (restyle.py owns most keys)
      listing.json          the cheap item list (titles, kinds), before a fetch
      bodies/<Kind>_<id>.html   the originals, theme assets stripped
      styled/<Kind>_<id>.html   the restyled versions
      verify-report.json    one record per styled file, with its sha256
      fixes.json            per-item opt-outs chosen in the review pane
      push-result.json      the last push: what was written, live re-verify
      pushed/<stamp>/       the originals of everything that push replaced
      restore-result.json   the last restore

Nothing here is about students. Bodies are instructor-authored content.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import restyle

AREA = "a11y"
LOOKS = restyle.LOOKS

KIND_LABEL = {"page": "Page", "assignment": "Assignment", "discussion": "Discussion",
              "quiz": "Quiz", "syllabus": "Syllabus", "announcement": "Announcement",
              "question": "Question"}
PLURAL = {"page": "pages", "assignment": "assignments", "discussion": "discussions",
          "quiz": "quizzes", "syllabus": "the syllabus", "announcement": "announcements",
          "question": "quiz questions"}

THEME_LINK = re.compile(r"<link\b[^>]*instructure-uploads[^>]*>", re.I)
THEME_SCRIPT = re.compile(r"<script\b[^>]*instructure-uploads[^>]*>\s*</script>", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def workdir(course_dir) -> Path:
    path = Path(course_dir) / AREA
    path.mkdir(parents=True, exist_ok=True)
    return path


def strip_theme(html: str) -> str:
    """Canvas prepends/appends the account theme <link>/<script> ON READ. They
    are not stored content; pushing them back would double-inject them."""
    if not html:
        return ""
    html = THEME_LINK.sub("", html)
    html = THEME_SCRIPT.sub("", html)
    return html.strip()


def kind_label(kind: str) -> str:
    return KIND_LABEL.get(str(kind).lower(), str(kind).capitalize())


def describe_counts(counts: dict) -> str:
    """{"page": 41, "assignment": 6, "discussion": 2} ->
    '41 pages, 6 assignments and 2 discussions'."""
    parts = []
    for kind in ("page", "assignment", "discussion", "quiz", "announcement", "question", "syllabus"):
        n = int(counts.get(kind) or 0)
        if not n:
            continue
        if kind == "syllabus":
            parts.append("the syllabus")
        elif n == 1:
            parts.append("1 " + kind.replace("quiz", "quiz"))
        else:
            parts.append("%d %s" % (n, PLURAL[kind]))
    for kind, n in counts.items():
        if kind not in PLURAL and n:
            parts.append("%d %s" % (n, kind))
    if not parts:
        return "nothing"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def fmt_size(chars: int) -> str:
    chars = int(chars or 0)
    if chars < 1024:
        return "%d B" % chars
    return "%.1f KB" % (chars / 1024.0)


# ------------------------------------------------------------- json files

def _read_json(path: Path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    tmp.replace(path)


def load_manifest(wd) -> dict | None:
    return _read_json(Path(wd) / "manifest.json")


def save_manifest(wd, manifest: dict) -> None:
    _write_json(Path(wd) / "manifest.json", manifest)


def load_listing(wd) -> dict | None:
    return _read_json(Path(wd) / "listing.json")


def save_listing(wd, listing: dict) -> None:
    _write_json(Path(wd) / "listing.json", listing)


def load_report(wd) -> list | None:
    data = _read_json(Path(wd) / "verify-report.json")
    return data if isinstance(data, list) else None


def load_fixes(wd) -> dict:
    data = _read_json(Path(wd) / "fixes.json", {}) or {}
    data.setdefault("excluded", [])
    return data


def save_fixes(wd, fixes: dict) -> None:
    _write_json(Path(wd) / "fixes.json", fixes)


def load_push_result(wd) -> dict | None:
    return _read_json(Path(wd) / "push-result.json")


def save_push_result(wd, result: dict) -> None:
    _write_json(Path(wd) / "push-result.json", result)


def load_restore_result(wd) -> dict | None:
    return _read_json(Path(wd) / "restore-result.json")


def save_restore_result(wd, result: dict) -> None:
    _write_json(Path(wd) / "restore-result.json", result)


def read_text(wd, rel_or_abs) -> str:
    with open(restyle.resolve_path(wd, rel_or_abs), encoding="utf-8") as fh:
        return fh.read()


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps the bytes as Canvas sent them; reading back in text
    # mode is what every digest and comparison uses, on every platform.
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def course_label(app, course_id) -> str:
    """The course name from the local course cache; never a Canvas call."""
    try:
        store = getattr(app, "store", None)
        rows = store.courses() if store is not None else []
    except Exception:  # noqa: BLE001
        rows = []
    for row in rows or []:
        if str(row.get("id")) == str(course_id):
            return row.get("name") or row.get("code") or "Course %s" % course_id
    return "Course %s" % course_id
