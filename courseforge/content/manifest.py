"""Load and validate the two manifest shapes the Build area pushes.

Pages-only manifest (Push-CanvasPages.ps1):
    {"course_label": "...", "root": "optional folder",
     "pages": [{"key": "...", "title": "...", "file": "...", "module": "Week 1",
                "module_position": 1, "position": 1}]}
    (`notion_id` is accepted as the key for old manifests.)

Project manifest (Push-CanvasProject.ps1; schema in knowledge/project-course.md):
    {"course_label", "syllabus_file", "pages": [{slug, title, file, front_page}],
     "assignments": [{key, name, file, points, submission_types, due_at, group}],
     "discussions": [{key, title, file, points, due_at, note, note_label}],
     "quizzes": [{key, title, file, group, time_limit, shuffle_answers, due_at,
                  questions: [{text, type, points, answers: [{text, correct}]}]}],
     "modules": [{name, items: [{type: Page|Assignment|Discussion|Quiz|SubHeader,
                                 slug|key|title}]}]}

Two Studio extensions, both optional: any entry may carry inline `html`
instead of `file`, and the manifest may name a `root` folder that relative
`file` paths resolve against. Without a root, paths resolve against the
course's build folder, then the manifest's own folder.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ITEM_TYPES = ("Page", "Assignment", "Discussion", "Quiz", "SubHeader")
SUBMISSION_TYPES = {"online_upload", "online_text_entry", "online_url", "media_recording",
                    "on_paper", "none", "external_tool", "discussion_topic", "online_quiz",
                    "student_annotation"}
QUESTION_TYPES = {"multiple_choice_question", "true_false_question", "short_answer_question",
                  "essay_question", "multiple_answers_question", "numerical_question",
                  "matching_question", "fill_in_multiple_blanks_question", "text_only_question"}


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("a manifest is a JSON object")
    return data


def detect_mode(manifest: dict) -> str:
    """'pages' for the lesson-course shape, 'project' for everything else."""
    explicit = str(manifest.get("mode") or "").strip().lower()
    if explicit in ("pages", "project"):
        return explicit
    if any(manifest.get(k) for k in ("modules", "assignments", "discussions", "quizzes", "syllabus_file")):
        return "project"
    pages = manifest.get("pages") or []
    if pages and any(p.get("slug") or p.get("front_page") for p in pages if isinstance(p, dict)):
        return "project"
    if pages and any(p.get("module") for p in pages if isinstance(p, dict)):
        return "pages"
    # Nothing here says "project": no modules, no graded items, no slugs, no
    # front page. Treat it as the lesson shape so validate() asks for the module
    # each page is missing, rather than silently checking it as a project.
    return "pages"


def page_key(page: dict) -> str:
    return str(page.get("key") or page.get("notion_id") or page.get("slug") or page.get("title") or "")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug[:80] or "page"


def resolve_root(manifest: dict, build_root: Path | None = None,
                 manifest_path: Path | None = None, explicit: Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if manifest.get("root"):
        return Path(str(manifest["root"]))
    if build_root:
        return Path(build_root)
    if manifest_path:
        return Path(manifest_path).parent
    return Path.cwd()


def resolve_file(entry: dict, root: Path) -> Path | None:
    name = entry.get("file")
    if not name:
        return None
    path = Path(str(name))
    return path if path.is_absolute() else Path(root) / path


def body_of(entry: dict, root: Path) -> str:
    """The HTML body of an entry: inline `html` wins, else the file."""
    if entry.get("html") is not None:
        return str(entry["html"])
    path = resolve_file(entry, root)
    if path is None:
        return ""
    if not path.is_file():
        raise FileNotFoundError(f"missing body file: {path}")
    return path.read_text(encoding="utf-8-sig")


def has_body(entry: dict) -> bool:
    return entry.get("html") is not None or bool(entry.get("file"))


def _entries(manifest: dict, key: str) -> list[dict]:
    rows = manifest.get(key) or []
    return [r for r in rows if isinstance(r, dict)]


def summary(manifest: dict) -> dict:
    mode = detect_mode(manifest)
    out = {"mode": mode, "course_label": manifest.get("course_label") or "",
           "pages": len(_entries(manifest, "pages")),
           "assignments": len(_entries(manifest, "assignments")),
           "discussions": len(_entries(manifest, "discussions")),
           "quizzes": len(_entries(manifest, "quizzes")),
           "modules": len(_entries(manifest, "modules")),
           "syllabus": bool(manifest.get("syllabus_file") or manifest.get("syllabus_html")),
           "questions": sum(len(q.get("questions") or []) for q in _entries(manifest, "quizzes"))}
    if mode == "pages":
        out["modules"] = len({p.get("module") for p in _entries(manifest, "pages") if p.get("module")})
    return out


def validate(manifest: dict, root: Path | None = None) -> list[str]:
    """Structural problems as plain sentences. Empty list means well-formed.
    File existence is verify_slots' job; this only checks shape and references."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["The manifest is not a JSON object."]
    mode = detect_mode(manifest)
    pages = manifest.get("pages")
    if pages is not None and not isinstance(pages, list):
        problems.append("pages must be a list.")
        pages = []
    pages = [p for p in (pages or []) if isinstance(p, dict)]
    if mode == "pages":
        if not pages:
            problems.append("A pages manifest needs at least one page.")
        seen: set[str] = set()
        for i, p in enumerate(pages, 1):
            label = p.get("title") or f"page {i}"
            if not p.get("title"):
                problems.append(f"Page {i} has no title.")
            if not has_body(p):
                problems.append(f"Page '{label}' has no file or html.")
            if not p.get("module"):
                problems.append(f"Page '{label}' names no module.")
            key = page_key(p)
            if key in seen:
                problems.append(f"Page key '{key}' appears twice.")
            seen.add(key)
        return problems

    # ---- project ----------------------------------------------------------
    slugs: set[str] = set()
    fronts = 0
    for i, p in enumerate(pages, 1):
        label = p.get("title") or p.get("slug") or f"page {i}"
        if not p.get("title"):
            problems.append(f"Page {i} has no title.")
        slug = p.get("slug") or slugify(p.get("title") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(slug)):
            problems.append(f"Page '{label}' has a slug that is not lowercase letters, digits and dashes: {slug!r}.")
        if slug in slugs:
            problems.append(f"Page slug '{slug}' appears twice.")
        slugs.add(slug)
        if not has_body(p):
            problems.append(f"Page '{label}' has no file or html.")
        if p.get("front_page"):
            fronts += 1
    if fronts > 1:
        problems.append("Only one page can be the front page.")

    keys: dict[str, set[str]] = {"assignments": set(), "discussions": set(), "quizzes": set()}
    for kind, name_field in (("assignments", "name"), ("discussions", "title"), ("quizzes", "title")):
        for i, e in enumerate(_entries(manifest, kind), 1):
            label = e.get(name_field) or e.get("key") or f"{kind[:-1]} {i}"
            if not e.get("key"):
                problems.append(f"The {kind[:-1]} '{label}' has no key.")
            elif e["key"] in keys[kind]:
                problems.append(f"The {kind[:-1]} key '{e['key']}' appears twice.")
            keys[kind].add(str(e.get("key")))
            if not e.get(name_field):
                problems.append(f"The {kind[:-1]} with key '{e.get('key')}' has no {name_field}.")
            if kind != "quizzes" and not has_body(e):
                problems.append(f"The {kind[:-1]} '{label}' has no file or html.")
            if e.get("points") is not None:
                try:
                    if float(e["points"]) < 0:
                        problems.append(f"'{label}' has negative points.")
                except (TypeError, ValueError):
                    problems.append(f"'{label}' has points that are not a number: {e['points']!r}.")
            if kind == "assignments":
                bad = [s for s in (e.get("submission_types") or []) if s not in SUBMISSION_TYPES]
                if bad:
                    problems.append(f"Assignment '{label}' has unknown submission types: {bad}.")
            if kind == "quizzes":
                qs = e.get("questions")
                if not isinstance(qs, list) or not qs:
                    problems.append(f"Quiz '{label}' has no questions.")
                else:
                    for j, q in enumerate(qs, 1):
                        if not isinstance(q, dict):
                            problems.append(f"Quiz '{label}' question {j} is not an object.")
                            continue
                        if q.get("type") and q["type"] not in QUESTION_TYPES:
                            problems.append(f"Quiz '{label}' question {j} has an unknown type {q['type']!r}.")

    for i, m in enumerate(_entries(manifest, "modules"), 1):
        mlabel = m.get("name") or f"module {i}"
        if not m.get("name"):
            problems.append(f"Module {i} has no name.")
        for j, it in enumerate(m.get("items") or [], 1):
            if not isinstance(it, dict):
                problems.append(f"Module '{mlabel}' item {j} is not an object.")
                continue
            t = it.get("type")
            if t not in ITEM_TYPES:
                problems.append(f"Module '{mlabel}' item {j} has an unknown type {t!r}.")
            elif t == "Page":
                if str(it.get("slug") or "") not in slugs:
                    problems.append(f"Module '{mlabel}' points at page slug '{it.get('slug')}', which is not in pages.")
            elif t == "SubHeader":
                if not it.get("title"):
                    problems.append(f"Module '{mlabel}' item {j} is a SubHeader with no title.")
            else:
                bucket = {"Assignment": "assignments", "Discussion": "discussions", "Quiz": "quizzes"}[t]
                if str(it.get("key") or "") not in keys[bucket]:
                    problems.append(f"Module '{mlabel}' points at {bucket[:-1]} key '{it.get('key')}', "
                                    f"which is not in {bucket}.")
    if not pages and not any(keys.values()) and not manifest.get("syllabus_file"):
        problems.append("The manifest has nothing to push.")
    return problems
