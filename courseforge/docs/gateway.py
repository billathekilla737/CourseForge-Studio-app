"""One gateway for every document kind: List, Fetch, (fixes), Push.

The five PowerShell gateways this replaces (Remediate-CanvasPptx, -Docx,
-PdfText, -OfficeText, Triage-CanvasPdfs) shared one shape and differed only
in which files they matched, which scanner they ran and which fixes file the
person (or the model) wrote. That shape lives here once; a `Kind` says what
differs.

    List   course files, unfiltered, matched here by extension or content type
           (a server-side type filter drops files uploaded with no type).
    Fetch  download `original.<ext>` per file under data/<cid>/docs/<kind>/<id>/
           beside a file.json, then run the kind's scan into work/.
    fixes  work/fixes.json (alt text, titles, heading promotions) or
           work/map.json (find -> replace), written by describe.py or a person.
    Push   apply -> fixed.<ext>, verify, and only then upload over the
           original: same name, same folder, on_duplicate=overwrite, so every
           link in the course keeps working. Dry run unless told otherwise.

Originals are never overwritten. A file that changed on Canvas since it was
fetched gets its old round moved to history/ before a fresh download.

Nothing here talks to the confirm gate or the ledger directly: `push` takes a
`gate` callable so the web route can pass `req.app._gate(...)` and the CLI can
pass a typed-yes prompt, and it returns what was written so the caller records
it. Every Canvas call goes through `app.content`, the client that cannot reach
student data.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import canvas_files
from ..canvas_files import file_ext

AREA = "docs"


# --------------------------------------------------------------------------- kinds

class Adapter:
    """What differs per kind. Subclasses import their tool lazily so a missing
    library disables one kind, not the area."""

    module: str = ""

    def scan(self, original: Path, workdir: Path, options: dict) -> dict:
        raise NotImplementedError

    def apply(self, original: Path, fixes: dict, out: Path, options: dict) -> dict:
        raise NotImplementedError

    def verify(self, original: Path, fixed: Path, fixes: dict, options: dict) -> dict:
        raise NotImplementedError

    def summary(self, report: dict | None, fixes: dict | None) -> dict:
        """Counts the state table shows: issues, alt todo, hits, hazards."""
        return {}

    def has_fixes(self, report: dict | None, fixes: dict | None) -> bool:
        return False

    def changes(self, report: dict | None, fixes: dict | None) -> list[str]:
        """Plain phrases for the dry-run row: '3 alt texts', '1 slide title'."""
        return []


class _AltAdapter(Adapter):
    """pptx and docx: alt text, structure, opt-in appearance changes."""

    def _mod(self):
        import importlib
        return importlib.import_module(f"courseforge.docs.{self.module}")

    def scan(self, original, workdir, options):
        return self._mod().scan_report(str(original), str(workdir))

    def apply(self, original, fixes, out, options):
        return self._mod().apply(str(original), fixes, str(out))

    def verify(self, original, fixed, fixes, options):
        return self._mod().verify(str(original), str(fixed), fixes)

    def summary(self, report, fixes):
        report = report or {}
        fixes = fixes or {}
        images = report.get("images") or []
        alts = fixes.get("alts") or {}
        todo = [im for im in images if im.get("needs_alt")]
        done = [im for im in todo if alts.get(im["key"]) is not None]
        return {
            "issues": len(report.get("issues") or []),
            "hard_issues": report.get("hard_issues", len(report.get("issues") or [])),
            "report_only": report.get("report_only", 0),
            "images": len(images),
            "alt_todo": len(todo) - len(done),
            "alt_done": len(done),
            "untitled": len(report.get("untitled") or []),
            "tables_without_header": sum(1 for t in (report.get("tables") or [])
                                         if not t.get("header_row")),
            "heading_candidates": len(report.get("faux_heading_candidates") or []),
        }

    def has_fixes(self, report, fixes):
        fixes = fixes or {}
        if any(v is not None for v in (fixes.get("alts") or {}).values()):
            return True
        if any((v or "").strip() for v in (fixes.get("titles") or {}).values()):
            return True
        if any(v not in (None, "", 0, "0") for v in (fixes.get("headings") or {}).values()):
            return True
        if fixes.get("table_headers", True) and report:
            return any(not t.get("header_row") for t in (report.get("tables") or []))
        return False

    def changes(self, report, fixes):
        fixes = fixes or {}
        out = []
        alts = {k: v for k, v in (fixes.get("alts") or {}).items() if v is not None}
        described = sum(1 for v in alts.values() if (v or "").strip())
        decorative = len(alts) - described
        if described:
            out.append(_n(described, "alt text"))
        if decorative:
            out.append(_n(decorative, "decorative mark"))
        titles = sum(1 for v in (fixes.get("titles") or {}).values() if (v or "").strip())
        if titles:
            out.append(_n(titles, "slide title"))
        heads = sum(1 for v in (fixes.get("headings") or {}).values()
                    if v not in (None, "", 0, "0"))
        if heads:
            out.append(_n(heads, "heading promotion"))
        if fixes.get("table_headers", True) and report:
            n = sum(1 for t in (report.get("tables") or []) if not t.get("header_row"))
            if n:
                out.append(_n(n, "table header row"))
        return out


class PptxAdapter(_AltAdapter):
    module = "pptx"


class DocxAdapter(_AltAdapter):
    module = "docx"


class _MapAdapter(Adapter):
    """pdf-text and office-text: a find -> replace map, scanned with a regex."""

    def _mod(self):
        import importlib
        return importlib.import_module(f"courseforge.docs.{self.module}")

    def scan(self, original, workdir, options):
        pattern = options.get("pattern") or ""
        return self._mod().scan_file(str(original), pattern or r"(?!x)x")

    def summary(self, report, fixes):
        report = report or {}
        fixes = normalize_map(fixes)
        haz = []
        h = report.get("hazards") or {}
        if report.get("unsupported"):
            haz.append("legacy format: convert to .docx/.pptx/.xlsx first")
        if report.get("unreadable"):
            haz.append("unreadable: %s" % report.get("reason", ""))
        if h.get("signed"):
            haz.append("digitally signed: editing invalidates the signature")
        if h.get("low_text_pages"):
            haz.append("pages with little or no text: %s" %
                       ", ".join(map(str, h["low_text_pages"][:8])))
        if h.get("toc_entries"):
            haz.append("has an outline with %d entries" % h["toc_entries"])
        return {
            "hits": len(report.get("hits") or []),
            "issues": len(report.get("hits") or []),
            "hard_issues": len(report.get("hits") or []),
            "pages": report.get("pages"),
            "hazards": haz,
            "mappings": len(fixes.get("map") or []),
            "fills": len(fixes.get("fill") or []),
            "unsupported": bool(report.get("unsupported")),
        }

    def has_fixes(self, report, fixes):
        fixes = normalize_map(fixes)
        return bool(fixes.get("map") or fixes.get("fill") or
                    fixes.get("set_author") is not None or
                    fixes.get("set_title") is not None or
                    fixes.get("set_lastmodifiedby") is not None)

    def changes(self, report, fixes):
        fixes = normalize_map(fixes)
        out = []
        if fixes.get("map"):
            out.append(_n(len(fixes["map"]), "text replacement"))
        if fixes.get("fill"):
            out.append(_n(len(fixes["fill"]), "form value"))
        for k in ("set_author", "set_title", "set_lastmodifiedby"):
            if fixes.get(k) is not None:
                out.append(k.replace("set_", "").replace("lastmodifiedby", "last modified by"))
        return out


class PdfTextAdapter(_MapAdapter):
    module = "pdf_text"

    def apply(self, original, fixes, out, options):
        mod = self._mod()
        fixes = normalize_map(fixes)
        src = original
        result: dict[str, Any] = {"ok": True, "notes": [], "problems": []}
        if fixes.get("fill"):
            filled = out.with_name("filled.pdf")
            r = mod.fill_map(str(src), fixes["fill"], str(filled),
                             allow_signed=bool(fixes.get("allow_signed")),
                             strip_signature=bool(fixes.get("strip_signature")))
            result["fill"] = r
            result["notes"] += r.get("notes", [])
            if r.get("refused") or not r.get("ok"):
                result["ok"] = False
                result["refused"] = bool(r.get("refused"))
                result["problems"].append(
                    "form values were not written: " +
                    (r.get("reason") or "; ".join(
                        [u for u in r.get("unplaced", [])] +
                        ["#%s collides: %s" % (c["idx"], "; ".join(c["problems"]))
                         for c in r.get("collisions", [])]) or "see report"))
                return result
            src = filled
        r = mod.apply_map(str(src), fixes.get("map") or [], str(out),
                          set_author=fixes.get("set_author"), set_title=fixes.get("set_title"),
                          update_toc=bool(fixes.get("update_toc")),
                          allow_signed=bool(fixes.get("allow_signed")),
                          strip_signature=bool(fixes.get("strip_signature")))
        result["replace"] = r
        result["notes"] += r.get("notes", [])
        if r.get("refused"):
            result.update(ok=False, refused=True)
            result["problems"].append("the PDF is digitally signed; editing would invalidate "
                                      "the signature (choose strip signature or allow signed)")
        elif not r.get("ok"):
            result["ok"] = False
            result["problems"] += r.get("problems", [])
        return result

    def verify(self, original, fixed, fixes, options):
        fixes = normalize_map(fixes)
        r = self._mod().verify_file(str(original), str(fixed), fixes.get("map") or [])
        return {"ok": r["ok"], "checks": {"opens": r.get("opens", False),
                                          "pages_same": r.get("pages_same", False),
                                          "text_replaced": not any(r["residual"].values())},
                "problems": r["problems"], "residual": r["residual"]}


class OfficeTextAdapter(_MapAdapter):
    module = "office_text"

    def apply(self, original, fixes, out, options):
        fixes = normalize_map(fixes)
        r = self._mod().apply_map(str(original), fixes.get("map") or [], str(out),
                                  set_author=fixes.get("set_author"),
                                  set_lastmodifiedby=fixes.get("set_lastmodifiedby"))
        r.setdefault("problems", [])
        if r.get("refused"):
            r["problems"].append(r.get("reason", "unsupported file"))
        return r

    def verify(self, original, fixed, fixes, options):
        fixes = normalize_map(fixes)
        r = self._mod().verify_file(str(fixed), fixes.get("map") or [])
        return {"ok": r["ok"], "checks": {"zip_ok": r["zip_ok"],
                                          "text_replaced": not any(r["residual"].values())},
                "problems": r["problems"], "residual": r["residual"]}


class TriageAdapter(Adapter):
    module = "pdf_triage"

    def scan(self, original, workdir, options):
        from . import pdf_triage
        return pdf_triage.triage_one(str(original))

    def summary(self, report, fixes):
        report = report or {}
        return {"cls": report.get("cls"), "severity": report.get("severity"),
                "pages": report.get("pages"), "note": report.get("note"),
                "issues": 1 if (report.get("severity") or 0) >= 2 else 0,
                "hard_issues": 1 if (report.get("severity") or 0) >= 2 else 0}


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    singular: str
    plural: str
    exts: tuple
    legacy_exts: tuple
    adapter: Adapter
    fixes_name: str
    tools: tuple
    needs_pattern: bool = False
    report_only: bool = False
    has_alt: bool = False
    scan_name: str = "report.json"
    describe_label: str = ""
    verb: str = "fixed"


def _n(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


KINDS: dict[str, Kind] = {
    "pptx": Kind("pptx", "PowerPoint", "PowerPoint file", "PowerPoint files",
                 (".pptx",), (".ppt",), PptxAdapter(), "fixes.json",
                 ("python_pptx",), has_alt=True, describe_label="Describe images with Claude"),
    "docx": Kind("docx", "Word", "Word document", "Word documents",
                 (".docx",), (".doc",), DocxAdapter(), "fixes.json",
                 ("python_docx",), has_alt=True, describe_label="Describe images with Claude"),
    "pdf-text": Kind("pdf-text", "PDF text", "PDF file", "PDF files",
                     (".pdf",), (), PdfTextAdapter(), "map.json",
                     ("pymupdf",), needs_pattern=True, scan_name="scan.json", verb="edited"),
    "office-text": Kind("office-text", "Office text", "Office file", "Office files",
                        (".docx", ".pptx", ".xlsx"), (".doc", ".ppt", ".xls"),
                        OfficeTextAdapter(), "map.json", (), needs_pattern=True,
                        scan_name="scan.json", verb="edited"),
    "triage": Kind("triage", "PDF triage", "PDF file", "PDF files",
                   (".pdf",), (), TriageAdapter(), "", ("pypdf",),
                   report_only=True, scan_name="triage.json"),
}


def kind_of(kind_id: str) -> Kind:
    try:
        return KINDS[kind_id]
    except KeyError:
        raise ValueError(f"unknown document kind {kind_id!r}; one of {', '.join(KINDS)}") from None


CONTENT_TYPES = {ext: ct for ct, ext in canvas_files.DOC_TYPES.items()}


# --------------------------------------------------------------------------- helpers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    tmp.replace(path)


def sha_of(data) -> str:
    blob = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def normalize_map(fixes) -> dict:
    """map.json may be the old bare list [{find, replace}] or a dict with
    map / fill / set_author / set_title / update_toc / allow_signed / strip_signature."""
    if fixes is None:
        return {"map": []}
    if isinstance(fixes, list):
        return {"map": [m for m in fixes if isinstance(m, dict) and m.get("find")]}
    if isinstance(fixes, dict):
        out = dict(fixes)
        raw = out.get("map") or []
        if isinstance(raw, dict):
            raw = [{"find": k, "replace": v} for k, v in raw.items()]
        out["map"] = [m for m in raw if isinstance(m, dict) and m.get("find")]
        out["fill"] = [f for f in (out.get("fill") or []) if isinstance(f, dict) and f.get("text")]
        return out
    return {"map": []}


class _Quiet:
    """A log that goes nowhere, for library callers and tests."""

    def __call__(self, message, done=None, total=None):
        pass

    def item(self, key, state, detail="", finished=False):
        pass


class Printer:
    """A log that prints, for the command line."""

    def __call__(self, message, done=None, total=None):
        print(message if total is None else f"[{done}/{total}] {message}")

    def item(self, key, state, detail="", finished=False):
        pass


QUIET = _Quiet()


class Refused(Exception):
    """A push the person did not confirm on the command line."""


def kind_dir(app, cid, kind: Kind) -> Path:
    path = Path(app.course_dir(cid)) / AREA / kind.id
    path.mkdir(parents=True, exist_ok=True)
    return path


def item_dir(app, cid, kind: Kind, file_id) -> Path:
    return kind_dir(app, cid, kind) / str(file_id)


def course_name(app, cid) -> str:
    try:
        for c in app.store.courses() or []:
            if str(c.get("id")) == str(cid):
                return c.get("name") or c.get("course_code") or f"course {cid}"
    except Exception:  # noqa: BLE001
        pass
    return f"course {cid}"


def tool_needs(app, kind: Kind) -> list[str]:
    """Names of the missing tools this kind depends on, or []."""
    if not kind.tools:
        return []
    try:
        from .. import tools
        info = tools.detect(getattr(app, "cfg", None))
    except Exception:  # noqa: BLE001
        return []
    return [t for t in kind.tools if not (info.get(t) or {}).get("ok")]


# --------------------------------------------------------------------------- per item

@dataclass
class Item:
    meta: dict
    dir: Path
    kind: Kind
    report: dict | None = None
    fixes: dict | None = None
    verify: dict | None = None
    push: dict | None = None
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return str(self.meta.get("id"))

    @property
    def ext(self) -> str:
        return self.meta.get("ext") or file_ext(self.meta)

    @property
    def original(self) -> Path:
        return self.dir / f"original{self.ext}"

    @property
    def fixed(self) -> Path:
        return self.dir / f"fixed{self.ext}"

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def fixes_path(self) -> Path:
        return self.work / self.kind.fixes_name

    @property
    def report_path(self) -> Path:
        return self.work / self.kind.scan_name

    @property
    def legacy(self) -> bool:
        return self.ext in self.kind.legacy_exts

    @property
    def fixes_sha(self) -> str:
        return sha_of(self.fixes or {})

    def load(self) -> "Item":
        self.report = read_json(self.report_path)
        self.fixes = read_json(self.fixes_path) if self.kind.fixes_name else None
        self.verify = read_json(self.dir / "verify.json")
        self.push = read_json(self.dir / "push.json")
        return self

    def state(self) -> str:
        if self.legacy:
            return "unsupported"
        if not self.original.is_file():
            return "not fetched"
        if self.kind.report_only:
            return "triaged" if self.report else "not scanned"
        if self.report is None:
            return "not scanned"
        if self.report.get("unsupported") or self.report.get("unreadable"):
            return "unsupported"
        has = self.kind.adapter.has_fixes(self.report, self.fixes)
        if self.push and self.push.get("fixes_sha") == self.fixes_sha and has:
            return "pushed"
        if not has:
            return "scanned"
        if self.verify and self.verify.get("fixes_sha") == self.fixes_sha:
            return "verified" if self.verify.get("ok") else "failed"
        return "fixes ready"

    def row(self) -> dict:
        s = self.kind.adapter.summary(self.report, self.fixes) if self.report else {}
        fetched = self.meta.get("fetched_at")
        return {
            "id": self.id,
            "name": self.meta.get("display_name") or "",
            "folder_id": self.meta.get("folder_id"),
            "folder": self.meta.get("folder") or "",
            "size": self.meta.get("size"),
            "content_type": self.meta.get("content_type") or "",
            "ext": self.ext,
            "modified": self.meta.get("updated_at") or self.meta.get("modified_at") or "",
            "state": self.state(),
            "fetched_at": fetched,
            "scanned": self.report is not None,
            "has_fixes": bool(self.kind.adapter.has_fixes(self.report, self.fixes)) if self.report else False,
            "changes": self.kind.adapter.changes(self.report, self.fixes) if self.report else [],
            "verify": ({"ok": self.verify.get("ok"), "checks": self.verify.get("checks", {}),
                        "problems": self.verify.get("problems", []),
                        "remaining_hard": self.verify.get("remaining_hard"),
                        "stale": self.verify.get("fixes_sha") != self.fixes_sha}
                       if self.verify else None),
            "pushed_at": (self.push or {}).get("at"),
            "canvas_changed": bool(self.extra.get("canvas_changed")),
            **s,
        }


def _meta_from_canvas(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "display_name": f.get("display_name") or f.get("filename") or "",
        "folder_id": f.get("folder_id"),
        "size": f.get("size"),
        "content_type": f.get("content-type") or f.get("content_type") or "",
        "url": f.get("url") or "",
        "updated_at": f.get("updated_at") or f.get("modified_at") or "",
        "ext": file_ext(f),
        "hidden": bool(f.get("hidden")),
        "locked": bool(f.get("locked")),
    }


def listed_files(app, cid, kind: Kind) -> dict:
    return read_json(kind_dir(app, cid, kind) / "files.json", {"at": None, "files": [], "folders": {}}) or \
        {"at": None, "files": [], "folders": {}}


def items(app, cid, kind: Kind, file_ids=None) -> list[Item]:
    """Every listed file as an Item, with local state loaded. Files that were
    fetched but have since left the listing are kept, marked missing."""
    listed = listed_files(app, cid, kind)
    folders = listed.get("folders") or {}
    wanted = {str(x) for x in (file_ids or [])} or None
    out: list[Item] = []
    seen = set()
    for f in listed.get("files") or []:
        fid = str(f.get("id"))
        if wanted and fid not in wanted:
            continue
        seen.add(fid)
        d = item_dir(app, cid, kind, fid)
        meta = dict(f)
        saved = read_json(d / "file.json")
        if saved:
            meta["fetched_at"] = saved.get("fetched_at")
            meta["fetched_size"] = saved.get("size")
        meta["folder"] = folders.get(str(f.get("folder_id")), "")
        it = Item(meta=meta, dir=d, kind=kind).load()
        if saved and saved.get("size") not in (None, f.get("size")) and it.original.is_file():
            it.extra["canvas_changed"] = it.push is None or it.push.get("size") != f.get("size")
        out.append(it)
    base = kind_dir(app, cid, kind)
    for d in sorted(p for p in base.iterdir() if p.is_dir() and p.name.isdigit()):
        if d.name in seen or (wanted and d.name not in wanted):
            continue
        saved = read_json(d / "file.json")
        if not saved:
            continue
        saved["folder"] = folders.get(str(saved.get("folder_id")), "")
        it = Item(meta=saved, dir=d, kind=kind).load()
        it.extra["missing_on_canvas"] = bool(listed.get("at"))
        out.append(it)
    out.sort(key=lambda it: (it.meta.get("folder") or "", (it.meta.get("display_name") or "").lower()))
    return out


# --------------------------------------------------------------------------- verbs

def list_files(app, cid, kind: Kind, log=QUIET) -> dict:
    """Refresh the course file list from Canvas and keep the ones this kind handles."""
    log(f"reading the file list of {course_name(app, cid)}")
    raw = app.content.course_files(cid)
    match = set(kind.exts) | set(kind.legacy_exts)
    files = [_meta_from_canvas(f) for f in raw if file_ext(f) in match]
    folders: dict[str, str] = {}
    try:
        for fo in app.content.course_folders(cid):
            folders[str(fo.get("id"))] = fo.get("full_name") or fo.get("name") or ""
    except Exception as exc:  # noqa: BLE001
        log(f"could not read folder names ({exc}); files are listed without them")
    data = {"at": now_iso(), "files": files, "folders": folders, "total_files": len(raw)}
    write_json(kind_dir(app, cid, kind) / "files.json", data)
    log(f"{len(files)} {kind.plural if len(files) != 1 else kind.singular} among {len(raw)} files")
    return {"listed": len(files), "total": len(raw), "at": data["at"]}


def _archive_round(it: Item, log) -> None:
    """Canvas has a different file than the one fetched: keep the old round."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    hist = it.dir / "history" / stamp
    hist.mkdir(parents=True, exist_ok=True)
    for p in list(it.dir.iterdir()):
        if p.name == "history":
            continue
        shutil.move(str(p), str(hist / p.name))
    log(f"{it.meta.get('display_name')}: the file on Canvas changed since it was fetched; "
        f"the earlier round is kept under history/{stamp}")


def scan_item(app, cid, kind: Kind, it: Item, options: dict | None = None, log=QUIET) -> dict:
    options = dict(options or {})
    if kind.needs_pattern and not options.get("pattern"):
        options["pattern"] = current_pattern(app, cid, kind)
    it.work.mkdir(parents=True, exist_ok=True)
    report = kind.adapter.scan(it.original, it.work, options)
    if kind.needs_pattern:
        report["pattern"] = options.get("pattern") or ""
    report["scanned_at"] = now_iso()
    write_json(it.report_path, report)
    it.report = report
    return report


def current_pattern(app, cid, kind: Kind) -> str:
    return (read_json(kind_dir(app, cid, kind) / "pattern.json", {}) or {}).get("pattern", "")


def set_pattern(app, cid, kind: Kind, pattern: str, log=QUIET) -> dict:
    """Remember the regex for this kind and rescan every fetched file with it."""
    if not kind.needs_pattern:
        raise ValueError(f"{kind.label} does not scan with a pattern")
    pattern = (pattern or "").strip()
    if pattern:
        re.compile(pattern)
    write_json(kind_dir(app, cid, kind) / "pattern.json", {"pattern": pattern, "at": now_iso()})
    scanned = 0
    todo = [it for it in items(app, cid, kind) if it.original.is_file() and not it.legacy]
    for n, it in enumerate(todo, 1):
        log(f"scanning {it.meta.get('display_name')}", n, len(todo))
        scan_item(app, cid, kind, it, {"pattern": pattern}, log)
        scanned += 1
    return {"pattern": pattern, "scanned": scanned}


def fetch(app, cid, kind: Kind, file_ids=None, pattern: str | None = None,
          log=QUIET, refresh_list: bool = True) -> dict:
    """Download the originals (once) and scan them."""
    if refresh_list or not listed_files(app, cid, kind).get("at"):
        list_files(app, cid, kind, log)
    if kind.needs_pattern and pattern is not None:
        pattern = pattern.strip()
        if pattern:
            re.compile(pattern)
        write_json(kind_dir(app, cid, kind) / "pattern.json", {"pattern": pattern, "at": now_iso()})
    options = {"pattern": current_pattern(app, cid, kind)} if kind.needs_pattern else {}
    todo = [it for it in items(app, cid, kind, file_ids) if not it.extra.get("missing_on_canvas")]
    fetched = scanned = skipped = 0
    unsupported: list[str] = []
    failures: list[dict] = []
    total = len(todo)
    for n, it in enumerate(todo, 1):
        name = it.meta.get("display_name") or it.id
        if it.legacy:
            unsupported.append(name)
            it.dir.mkdir(parents=True, exist_ok=True)
            write_json(it.dir / "file.json", {**_saved_meta(it.meta), "fetched_at": None,
                                              "unsupported": "legacy binary Office format"})
            log(f"{name}: legacy format, cannot be read here; convert it to "
                f"{'.pptx' if it.ext == '.ppt' else '.docx' if it.ext == '.doc' else '.xlsx'} first",
                n, total)
            continue
        try:
            if it.original.is_file() and it.extra.get("canvas_changed"):
                _archive_round(it, log)
                it.load()
            if it.original.is_file():
                skipped += 1
                log(f"{name}: already fetched, keeping the original", n, total)
            else:
                log(f"downloading {name}", n, total)
                log.item(name, "working", "downloading")
                it.dir.mkdir(parents=True, exist_ok=True)
                app.content.download_file(it.meta["url"], it.original, it.meta.get("size"))
                fetched += 1
            write_json(it.dir / "file.json", {**_saved_meta(it.meta), "fetched_at": now_iso()})
            log.item(name, "working", "scanning")
            scan_item(app, cid, kind, it, options, log)
            scanned += 1
            log.item(name, "done", "", finished=True)
        except Exception as exc:  # noqa: BLE001
            failures.append({"id": it.id, "name": name, "error": f"{type(exc).__name__}: {exc}"})
            log(f"{name}: {type(exc).__name__}: {exc}", n, total)
            log.item(name, "error", str(exc)[:80], finished=True)
    if kind.report_only:
        write_triage(app, cid, kind)
    out = {"fetched": fetched, "scanned": scanned, "kept": skipped,
           "unsupported": unsupported, "failures": failures}
    log(f"fetched {fetched}, scanned {scanned}, kept {skipped} already on this computer"
        + (f", {len(unsupported)} legacy files not readable" if unsupported else "")
        + (f", {len(failures)} failed" if failures else ""))
    return out


def _saved_meta(meta: dict) -> dict:
    keep = ("id", "display_name", "folder_id", "size", "content_type", "ext", "updated_at", "url")
    return {k: meta.get(k) for k in keep}


def write_triage(app, cid, kind: Kind) -> list[dict]:
    """The ranked census, worst first, for the report-only kind."""
    rows = []
    for it in items(app, cid, kind):
        if not it.report:
            continue
        rows.append({"id": it.id, "name": it.meta.get("display_name"), "folder": it.meta.get("folder"),
                     "size": it.meta.get("size"), **{k: it.report.get(k) for k in
                                                     ("cls", "severity", "pages", "text_pages",
                                                      "image_pages", "tagged", "note")}})
    rows.sort(key=lambda r: (-(r.get("severity") or 0), (r.get("name") or "").lower()))
    write_json(kind_dir(app, cid, kind) / "triage.json", {"at": now_iso(), "rows": rows})
    return rows


def save_fixes(app, cid, kind: Kind, file_id, incoming: dict) -> dict:
    """Store a person's edits. Any alt or title that differs from what was saved
    before is marked as written by a human, so a later describe run leaves it."""
    if not kind.fixes_name:
        raise ValueError(f"{kind.label} is report-only; there is nothing to save")
    it = next((i for i in items(app, cid, kind, [file_id])), None)
    if it is None:
        raise FileNotFoundError(f"file {file_id} is not in the {kind.label} list; refresh the list first")
    if not it.original.is_file():
        raise FileNotFoundError(f"{it.meta.get('display_name')} has not been fetched yet")
    current = it.fixes or {}
    if kind.fixes_name == "map.json":
        fixes = normalize_map(incoming)
        fixes["saved_at"] = now_iso()
    else:
        fixes = _merge_human(current, incoming or {})
    write_json(it.fixes_path, fixes)
    it.fixes = fixes
    return {"file_id": it.id, "fixes": fixes, "state": it.state(), "row": it.row()}


def _merge_human(current: dict, incoming: dict) -> dict:
    out = dict(current)
    for field_name, src_name in (("alts", "sources"), ("titles", "title_sources")):
        new = incoming.get(field_name)
        if new is None:
            continue
        old = current.get(field_name) or {}
        sources = dict(current.get(src_name) or {})
        merged = {}
        for k, v in new.items():
            if v is None:
                sources.pop(k, None)
                continue
            merged[k] = v
            if k not in old or old.get(k) != v:
                sources[k] = "human"
            else:
                sources.setdefault(k, "human")
        out[field_name] = merged
        out[src_name] = {k: s for k, s in sources.items() if k in merged}
    if "headings" in incoming:
        out["headings"] = {str(k): v for k, v in (incoming.get("headings") or {}).items()
                           if v not in (None, "", 0, "0")}
    if "table_headers" in incoming:
        out["table_headers"] = bool(incoming["table_headers"])
    out.setdefault("table_headers", True)
    out["saved_at"] = now_iso()
    return out


def prepare(app, cid, kind: Kind, it: Item, options: dict | None = None, log=QUIET) -> dict:
    """apply -> fixed.<ext>, then verify. Writes verify.json. Never touches Canvas."""
    options = options or {}
    name = it.meta.get("display_name") or it.id
    fixes = it.fixes if it.fixes is not None else ({"table_headers": True} if kind.fixes_name == "fixes.json" else {})
    result = {"file_id": it.id, "name": name, "fixes_sha": it.fixes_sha, "at": now_iso()}
    try:
        if it.fixed.exists():
            it.fixed.unlink()
        applied = kind.adapter.apply(it.original, fixes, it.fixed, options)
        result["applied"] = applied
        if applied.get("refused") or applied.get("ok") is False:
            result.update(ok=False, checks={}, problems=applied.get("problems") or
                          [applied.get("reason") or "apply failed"], refused=bool(applied.get("refused")))
        else:
            ver = kind.adapter.verify(it.original, it.fixed, fixes, options)
            result.update(ver)
            result["ok"] = bool(ver.get("ok"))
    except Exception as exc:  # noqa: BLE001
        result.update(ok=False, checks={}, problems=[f"{type(exc).__name__}: {exc}"])
    if not result["ok"] and it.fixed.exists():
        # a file that failed verify must never be mistaken for one that passed
        try:
            it.fixed.rename(it.dir / f"rejected{it.ext}")
        except OSError:
            it.fixed.unlink(missing_ok=True)
    result["fixed_size"] = it.fixed.stat().st_size if it.fixed.is_file() else None
    result["original_size"] = it.original.stat().st_size if it.original.is_file() else None
    write_json(it.dir / "verify.json", result)
    it.verify = result
    return result


def plan(app, cid, kind: Kind, file_ids=None, options: dict | None = None, log=QUIET) -> dict:
    """The dry run: apply and verify every item with fixes, upload nothing."""
    if kind.report_only:
        raise ValueError(f"{kind.label} is report-only; nothing is ever uploaded")
    rows, blocked = [], []
    todo = [it for it in items(app, cid, kind, file_ids)
            if it.original.is_file() and not it.legacy and it.report is not None
            and kind.adapter.has_fixes(it.report, it.fixes)]
    for n, it in enumerate(todo, 1):
        name = it.meta.get("display_name") or it.id
        log(f"applying and checking {name}", n, len(todo))
        log.item(name, "working", "apply and verify")
        res = prepare(app, cid, kind, it, options, log)
        log.item(name, "done" if res["ok"] else "error", "", finished=True)
        row = {
            "file_id": it.id, "name": name, "folder": it.meta.get("folder") or "",
            "folder_id": it.meta.get("folder_id"),
            "from": res.get("original_size"), "to": res.get("fixed_size"),
            "changes": kind.adapter.changes(it.report, it.fixes),
            "ok": res["ok"], "checks": res.get("checks", {}), "problems": res.get("problems", []),
            "remaining_hard": res.get("remaining_hard"),
            "already_pushed": bool(it.push and it.push.get("fixes_sha") == it.fixes_sha),
        }
        (rows if res["ok"] else blocked).append(row)
    fresh = [r for r in rows if not r["already_pushed"]]
    return {"kind": kind.id, "course_id": str(cid), "rows": rows, "blocked": blocked,
            "uploads": fresh, "count": len(fresh), "dry_run": True,
            "sentence": push_sentence(app, cid, kind, len(fresh)),
            "fingerprint": _fingerprint(cid, kind, fresh, app)}


def _fingerprint(cid, kind: Kind, rows: list[dict], app) -> dict:
    files = []
    for r in rows:
        it = Item(meta={"id": r["file_id"], "ext": ""}, dir=item_dir(app, cid, kind, r["file_id"]), kind=kind)
        saved = read_json(it.dir / "file.json") or {}
        it.meta["ext"] = saved.get("ext") or ""
        it.load()
        files.append({"id": str(r["file_id"]), "fixes": it.fixes_sha})
    files.sort(key=lambda f: f["id"])
    return {"course_id": str(cid), "kind": kind.id, "files": files}


def push_sentence(app, cid, kind: Kind, n: int) -> str:
    name = course_name(app, cid)
    what = f"1 {kind.verb} {kind.singular} over its original" if n == 1 else \
        f"{n} {kind.verb} {kind.plural} over their originals"
    tail = ("The text you mapped is replaced; names, folders and links stay the same; "
            "the originals are kept on this computer." if kind.needs_pattern else
            "Names, folders and links stay the same; the originals are kept on this computer.")
    return f"Upload {what} in {name}. {tail}"


def push(app, cid, kind: Kind, file_ids=None, apply: bool = False,
         gate: Callable[[dict, str, list], None] | None = None,
         options: dict | None = None, log=QUIET) -> dict:
    """Dry run by default. With apply=True: gate, upload the verified files,
    read the listing back, and return what was written for the ledger."""
    p = plan(app, cid, kind, file_ids, options, log)
    if not apply:
        log(f"dry run: {p['count']} would be uploaded, {len(p['blocked'])} would not")
        return p
    if p["count"] == 0:
        p["dry_run"] = False
        p["uploaded"] = []
        log("nothing to upload: no verified files with fixes that are not already on Canvas")
        return p
    detail = [{"label": r["name"], "from": _kb(r["from"]), "to": _kb(r["to"]),
               "note": ", ".join(r["changes"])} for r in p["uploads"]]
    if gate is not None:
        gate(p["fingerprint"], p["sentence"], detail)
    uploaded, failed = [], []
    total = len(p["uploads"])
    for n, r in enumerate(p["uploads"], 1):
        it = next(i for i in items(app, cid, kind, [r["file_id"]]))
        name = it.meta.get("display_name") or it.id
        if not (it.verify and it.verify.get("ok") and it.verify.get("fixes_sha") == it.fixes_sha
                and it.fixed.is_file()):
            failed.append({"file_id": it.id, "name": name, "error": "not verified; refused"})
            log(f"{name}: refused, the fixed file did not pass verify", n, total)
            continue
        ct = CONTENT_TYPES.get(it.ext) or it.meta.get("content_type") or "application/octet-stream"
        log(f"uploading {name} over the original ({_kb(it.fixed.stat().st_size)})", n, total)
        log.item(name, "working", "uploading")
        try:
            resp = app.content.upload_course_file(cid, name, it.fixed, folder_id=it.meta.get("folder_id"),
                                                  content_type=ct, on_duplicate="overwrite")
        except Exception as exc:  # noqa: BLE001
            failed.append({"file_id": it.id, "name": name, "error": f"{type(exc).__name__}: {exc}"})
            log(f"{name}: upload failed: {exc}", n, total)
            log.item(name, "error", str(exc)[:80], finished=True)
            continue
        rec = {"at": now_iso(), "new_id": (resp or {}).get("id"), "size": it.fixed.stat().st_size,
               "fixes_sha": it.fixes_sha, "folder_id": it.meta.get("folder_id"),
               "display_name": name, "verified_back": None}
        write_json(it.dir / "push.json", rec)
        uploaded.append({"file_id": it.id, "name": name, "new_id": rec["new_id"], "size": rec["size"]})
        log.item(name, "done", "", finished=True)
    # Read back once and pair by (display_name, folder_id): an overwrite gets a fresh id.
    if uploaded:
        try:
            listing = app.content.course_files(cid)
            by_key = {(f.get("display_name"), str(f.get("folder_id"))): f for f in listing}
            for u in uploaded:
                d = item_dir(app, cid, kind, u["file_id"])
                rec = read_json(d / "push.json") or {}
                f = by_key.get((u["name"], str(rec.get("folder_id"))))
                ok = bool(f) and f.get("size") == u["size"]
                rec["verified_back"] = ok
                rec["canvas_id"] = (f or {}).get("id")
                write_json(d / "push.json", rec)
                u["verified_back"] = ok
                log(f"{u['name']}: Canvas now holds {_kb((f or {}).get('size'))}"
                    f"{' (matches)' if ok else ' (does not match what was sent)'}")
            list_files(app, cid, kind, log)
        except Exception as exc:  # noqa: BLE001
            log(f"could not read the file list back ({exc}); the uploads are recorded unverified")
    p.update(dry_run=False, uploaded=uploaded, failed=failed,
             sentence_done=_done_sentence(app, cid, kind, len(uploaded)))
    return p


def _done_sentence(app, cid, kind: Kind, n: int) -> str:
    what = f"1 {kind.verb} {kind.singular}" if n == 1 else f"{n} {kind.verb} {kind.plural}"
    return f"Uploaded {what} over the originals in {course_name(app, cid)}; the originals are kept on this computer"


def restore_plan(app, cid, kind: Kind, file_ids=None) -> dict:
    rows = []
    for it in items(app, cid, kind, file_ids):
        if it.push and it.original.is_file():
            rows.append({"file_id": it.id, "name": it.meta.get("display_name"),
                         "folder_id": it.meta.get("folder_id"), "size": it.original.stat().st_size,
                         "pushed_at": it.push.get("at")})
    n = len(rows)
    sentence = (f"Put the original {kind.singular if n == 1 else kind.plural} back over "
                f"{'the fixed one' if n == 1 else f'the {n} fixed ones'} in {course_name(app, cid)}. "
                "Names, folders and links stay the same; the fixed copies stay on this computer.")
    return {"rows": rows, "count": n, "sentence": sentence,
            "fingerprint": {"course_id": str(cid), "kind": kind.id, "restore": sorted(r["file_id"] for r in rows)}}


def restore(app, cid, kind: Kind, file_ids=None, gate=None, log=QUIET) -> dict:
    """Upload the kept originals over the fixed files. Asks first, like push."""
    p = restore_plan(app, cid, kind, file_ids)
    if p["count"] == 0:
        return {**p, "restored": []}
    if gate is not None:
        gate(p["fingerprint"], p["sentence"], [{"label": r["name"], "from": "", "to": _kb(r["size"])} for r in p["rows"]])
    restored, failed = [], []
    for n, r in enumerate(p["rows"], 1):
        it = next(i for i in items(app, cid, kind, [r["file_id"]]))
        name = it.meta.get("display_name") or it.id
        ct = CONTENT_TYPES.get(it.ext) or "application/octet-stream"
        log(f"putting the original {name} back", n, p["count"])
        try:
            app.content.upload_course_file(cid, name, it.original, folder_id=it.meta.get("folder_id"),
                                           content_type=ct, on_duplicate="overwrite")
            push_rec = read_json(it.dir / "push.json") or {}
            push_rec["restored_at"] = now_iso()
            write_json(it.dir / "restored.json", push_rec)
            (it.dir / "push.json").unlink(missing_ok=True)
            restored.append({"file_id": it.id, "name": name})
        except Exception as exc:  # noqa: BLE001
            failed.append({"file_id": it.id, "name": name, "error": f"{type(exc).__name__}: {exc}"})
    return {**p, "restored": restored, "failed": failed}


def _kb(n) -> str:
    if n is None:
        return ""
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


# --------------------------------------------------------------------------- state

def state(app, cid, kind: Kind) -> dict:
    listed = listed_files(app, cid, kind)
    rows = [it.row() for it in items(app, cid, kind)]
    counts = {
        "files": len(rows),
        "fetched": sum(1 for r in rows if r["state"] not in ("not fetched", "unsupported")),
        "with_issues": sum(1 for r in rows if (r.get("hard_issues") or 0) > 0),
        "alt_todo": sum(r.get("alt_todo") or 0 for r in rows),
        "alt_done": sum(r.get("alt_done") or 0 for r in rows),
        "hits": sum(r.get("hits") or 0 for r in rows),
        "fixes_ready": sum(1 for r in rows if r["state"] in ("fixes ready", "verified", "failed")),
        "verified": sum(1 for r in rows if r["state"] == "verified"),
        "failed": sum(1 for r in rows if r["state"] == "failed"),
        "pushed": sum(1 for r in rows if r["state"] == "pushed"),
        "unsupported": sum(1 for r in rows if r["state"] == "unsupported"),
    }
    out = {
        "kind": kind.id, "label": kind.label, "course_id": str(cid),
        "listed_at": listed.get("at"), "total_files": listed.get("total_files"),
        "items": rows, "summary": counts, "needs": tool_needs(app, kind),
        "has_alt": kind.has_alt, "needs_pattern": kind.needs_pattern,
        "report_only": kind.report_only, "fixes_name": kind.fixes_name,
        "describe_label": kind.describe_label,
    }
    if kind.needs_pattern:
        out["pattern"] = current_pattern(app, cid, kind)
    if kind.report_only:
        out["triage"] = (read_json(kind_dir(app, cid, kind) / "triage.json") or {}).get("rows", [])
        out["summary"]["by_class"] = {}
        for r in out["triage"]:
            out["summary"]["by_class"][r.get("cls") or "?"] = out["summary"]["by_class"].get(r.get("cls") or "?", 0) + 1
    return out


def report_for(app, cid, kind: Kind, file_id) -> dict:
    """Everything the review pane needs for one file."""
    it = next((i for i in items(app, cid, kind, [file_id])), None)
    if it is None:
        raise FileNotFoundError(f"file {file_id} is not in the {kind.label} list")
    images = []
    for im in (it.report or {}).get("images") or []:
        images.append({k: im.get(k) for k in ("key", "hash", "slide", "current_alt", "decorative",
                                               "needs_alt", "slide_context", "name", "size_px", "ext")}
                      | {"has_picture": bool(im.get("path")) and Path(im["path"]).is_file()})
    return {"meta": _saved_meta(it.meta) | {"folder": it.meta.get("folder", "")},
            "row": it.row(), "report": it.report, "fixes": it.fixes, "verify": it.verify,
            "push": it.push, "images": images}


def hub_status(app, cid) -> dict:
    """Local state only; the hub paints in tens of milliseconds."""
    lines, needs, actions = [], [], []
    badge = 0
    for kind in KINDS.values():
        base = Path(app.course_dir(cid)) / AREA / kind.id
        if not base.is_dir():
            continue
        listed = read_json(base / "files.json")
        if not listed or not listed.get("files"):
            continue
        rows = [it.row() for it in items(app, cid, kind)]
        n = len(rows)
        if kind.report_only:
            tri = (read_json(base / "triage.json") or {}).get("rows", [])
            worst = sum(1 for r in tri if (r.get("severity") or 0) >= 3)
            lines.append(f"{kind.label}: {n} files, {worst} scanned images with no text" if tri
                         else f"{kind.label}: {n} files, not yet triaged")
            continue
        bits = [f"{n} {kind.plural if n != 1 else kind.singular}"]
        if kind.has_alt:
            todo = sum(r.get("alt_todo") or 0 for r in rows)
            if todo:
                bits.append(f"{todo} need alt text")
                badge += todo
        else:
            hits = sum(r.get("hits") or 0 for r in rows)
            if hits:
                bits.append(f"{hits} matches to review")
        verified = sum(1 for r in rows if r["state"] == "verified")
        pushed = sum(1 for r in rows if r["state"] == "pushed")
        failed = sum(1 for r in rows if r["state"] == "failed")
        if verified:
            bits.append(f"{verified} verified, not yet uploaded")
            badge += verified
        if failed:
            bits.append(f"{failed} failed verify")
        if pushed:
            bits.append(f"{pushed} pushed")
        lines.append(f"{kind.label}: " + ", ".join(bits))
        for t in tool_needs(app, kind):
            if t not in needs:
                needs.append(t)
    if not lines:
        lines.append("No document files listed yet. Fetch and scan downloads copies and "
                     "reads them; nothing is pushed.")
    actions.append({"label": "PowerPoint", "href": f"#/c/{cid}/docs/pptx"})
    actions.append({"label": "Word", "href": f"#/c/{cid}/docs/docx"})
    actions.append({"label": "PDF triage", "href": f"#/c/{cid}/docs/triage"})
    return {"lines": lines, "badge": badge or None, "needs": needs, "actions": actions}
