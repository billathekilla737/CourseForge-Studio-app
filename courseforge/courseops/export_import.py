"""Whole-course export, import and clone.

Ported from Export-CanvasCourse.ps1 and Import-CanvasCourse.ps1.

Export: start a content export, poll until Canvas says exported (the state
`waiting_for_external_tool` is just one it passes through), download the
cartridge to data/<course>/exports/, refuse a file under 1 KB, keep a history.

FERPA note, stated rather than discovered: a cartridge is course content, but
on a course that has been taught Canvas can bundle student-authored discussion
entries inside exported topics. Keep the file local. It is never uploaded
anywhere by this tool except back into Canvas as an import.

Import: exactly one source (another course, or an .imscc file). The
destination is this course, another course, or a new unpublished shell. A
destination that already has content is refused unless the person says
"add anyway", because an import adds and never replaces. Clone is an import
from this course into a new shell.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from .. import ledger
from ..canvas import CanvasError
from .common import (AREA, Gate, Log, Refused, append_history, area_dir, as_int,
                     course_label, load_json, now_iso, plural, quiet_log)

EXPORTS_HISTORY = "exports.json"
IMPORTS_HISTORY = "imports.json"
COUNT_KINDS = ("pages", "modules", "assignments", "quizzes", "discussions", "files")
GATE_KINDS = ("pages", "modules", "assignments", "quizzes", "discussions")


# ------------------------------------------------------------------ export
def exports_dir(app, course_id) -> Path:
    path = Path(app.course_dir(course_id)) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]", "_", name or "course")
    return re.sub(r"\s+", "_", text).strip("_") or "course"


def export_course(app, course_id, out_dir: Path | str | None = None, log: Log = quiet_log,
                  export_type: str = "common_cartridge", timeout_s: int = 1800) -> dict:
    if export_type not in ("common_cartridge", "zip"):
        raise ValueError("export type must be common_cartridge or imscc, or zip for files only")
    c = app.content
    course = c.course_detail(course_id) or {}
    label = course_label(course, course_id)
    out = Path(out_dir) if out_dir else exports_dir(app, course_id)
    out.mkdir(parents=True, exist_ok=True)
    log(f"Exporting {label} as {'a common cartridge' if export_type == 'common_cartridge' else 'a zip of files'}")

    info = c.start_export(course_id, export_type) or {}
    export_id = info.get("id")
    if not export_id:
        raise RuntimeError(f"Canvas did not start the export: {info}")
    log(f"export {export_id} started ({info.get('workflow_state', 'created')}); Canvas is packing the course")
    info = c.wait_export(course_id, export_id, timeout_s=timeout_s,
                         progress=lambda msg, d, t: log(msg, d, t))
    if info.get("workflow_state") != "exported":
        raise RuntimeError(f"Canvas reported the export failed (export id {export_id})")

    ext = "zip" if export_type == "zip" else "imscc"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = out / f"{course_id}_{safe_name(course.get('name') or '')}_{stamp}.{ext}"
    log("downloading the file")
    path = c.download_export(info, dest)          # refuses anything under 1 KB
    size = path.stat().st_size
    entry = {"at": now_iso(), "course_id": str(course_id), "course_name": course.get("name"),
             "type": export_type, "export_id": export_id, "path": str(path),
             "name": path.name, "bytes": size, "kb": round(size / 1024)}
    append_history(area_dir(app, course_id) / EXPORTS_HISTORY, entry)
    ledger.record(app.course_dir(course_id), AREA,
                  f"Exported {label} to {path.name} ({entry['kb']} KB), kept on this machine",
                  url=f"{app.cfg.base_url}/courses/{course_id}/content_exports", count=1, kind="export")
    log(f"EXPORTED -> {path} ({entry['kb']} KB). Keep this file local; a taught course's "
        "cartridge can carry student-written discussion text.")
    return entry


def export_history(app, course_id) -> list[dict]:
    rows = load_json(area_dir(app, course_id) / EXPORTS_HISTORY, []) or []
    for r in rows:
        r["exists"] = bool(r.get("path")) and Path(r["path"]).is_file()
    rows.reverse()
    return rows


def open_folder(path: Path | str) -> bool:
    """Show a folder in the desktop file manager. Local only; nothing leaves."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- cartridge
def cartridge_counts(path: Path | str) -> dict:
    """What an .imscc holds, from its imsmanifest.xml. Pages and assignments
    share one resource type in Common Cartridge, so they are reported together
    as 'items'."""
    path = Path(path)
    out = {"resources": 0, "quizzes": 0, "discussions": 0, "links": 0, "files": 0, "items": 0}
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("imsmanifest.xml")), None)
            if not name:
                out["note"] = "no imsmanifest.xml inside; not a Common Cartridge"
                return out
            root = ElementTree.fromstring(z.read(name))
    except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as exc:
        out["note"] = f"could not read the cartridge: {exc}"
        return out
    for el in root.iter():
        if not el.tag.endswith("resource"):
            continue
        typ = (el.get("type") or "").lower()
        out["resources"] += 1
        if "imsqti" in typ or "assessment" in typ:
            out["quizzes"] += 1
        elif "imsdt" in typ:
            out["discussions"] += 1
        elif "imswl" in typ:
            out["links"] += 1
        elif "learning-application-resource" in typ:
            out["items"] += 1
        elif typ == "webcontent":
            out["files"] += 1
    return out


# ------------------------------------------------------------------ import
def _counts_summary(counts: dict, kinds=GATE_KINDS) -> str:
    parts = [f"{as_int(counts.get(k))} {k}" for k in kinds if as_int(counts.get(k)) > 0]
    return ", ".join(parts) if parts else "nothing"


def accounts(app) -> list[dict]:
    return [{"id": a.get("id"), "name": a.get("name")} for a in (app.content.accounts() or [])]


def import_plan(app, dest_course_id=None, source_course_id=None, imscc: str | None = None,
                new_course: dict | None = None, force: bool = False, log: Log = quiet_log) -> dict:
    """Counts on both sides and the sentence that would be confirmed. Reads only."""
    if bool(source_course_id) == bool(imscc):
        raise ValueError("Pick exactly one source: another course, or an .imscc file.")
    if imscc and not Path(imscc).is_file():
        raise FileNotFoundError(f"file not found: {imscc}")
    if new_course is not None and not isinstance(new_course, dict):
        raise ValueError("new_course must be an object with name and account_id")
    if new_course and not (new_course.get("name") or "").strip():
        raise ValueError("A new shell needs a name.")
    if not new_course and not dest_course_id:
        raise ValueError("Pick a destination: this course, another course id, or a new shell.")
    c = app.content

    # -- source
    if source_course_id:
        if new_course is None and str(source_course_id) == str(dest_course_id):
            raise ValueError("The source and the destination are the same course.")
        log(f"counting what course {source_course_id} holds")
        src_course = c.course_detail(source_course_id) or {}
        src_counts = c.course_counts(source_course_id)
        source = {"mode": "course_copy", "id": str(source_course_id),
                  "name": course_label(src_course, source_course_id), "counts": src_counts,
                  "summary": _counts_summary(src_counts)}
        planned = {k: as_int(src_counts.get(k)) for k in COUNT_KINDS}
    else:
        p = Path(imscc)
        cc = cartridge_counts(p)
        source = {"mode": "cartridge", "path": str(p), "name": p.name,
                  "bytes": p.stat().st_size, "counts": cc,
                  "summary": (f"{cc['resources']} resources: {cc['items']} pages, assignments or files, "
                              f"{cc['quizzes']} quizzes, {cc['discussions']} discussions, {cc['links']} links")
                  if cc.get("resources") else (cc.get("note") or "an empty cartridge")}
        planned = {"resources": cc["resources"], "quizzes": cc["quizzes"], "discussions": cc["discussions"],
                   "items": cc["items"], "links": cc["links"]}

    # -- destination
    if new_course:
        acct = new_course.get("account_id")
        dest = {"new": True, "name": new_course["name"].strip(), "account_id": acct,
                "course_code": (new_course.get("course_code") or "").strip(),
                "counts": {k: 0 for k in COUNT_KINDS}, "populated": False}
        if not acct:
            dest["accounts"] = accounts(app)
            dest["needs_account"] = True
    else:
        log(f"counting what course {dest_course_id} already holds")
        dest_course = c.course_detail(dest_course_id) or {}
        dest_counts = c.course_counts(dest_course_id)
        dest = {"new": False, "id": str(dest_course_id), "name": course_label(dest_course, dest_course_id),
                "counts": dest_counts, "summary": _counts_summary(dest_counts),
                "populated": any(as_int(dest_counts.get(k)) > 0 for k in GATE_KINDS),
                "workflow_state": dest_course.get("workflow_state")}

    refusal = None
    if dest.get("populated") and not force:
        refusal = (f"{dest['name']} already has {dest['summary']}. An import adds on top of what is "
                   "there and can duplicate it. Tick Add anyway to import regardless.")
    if dest.get("needs_account"):
        refusal = refusal or "A new shell needs the account to create it in. Pick one from the list."

    sentence = confirm_sentence(source, dest)
    detail = []
    if source["mode"] == "course_copy":
        for k in GATE_KINDS:
            add = as_int(source["counts"].get(k))
            if add:
                have = as_int(dest["counts"].get(k))
                detail.append({"label": k.capitalize(), "from": str(have), "to": f"{have + add} (adds {add})"})
    else:
        for k in ("items", "quizzes", "discussions", "links"):
            if planned.get(k):
                detail.append({"label": k.capitalize(), "from": "",
                               "to": f"adds up to {planned[k]}"})
    return {"source": source, "destination": dest, "planned": planned, "force": bool(force),
            "refusal": refusal, "sentence": sentence, "detail": detail}


def confirm_sentence(source: dict, dest: dict) -> str:
    if source["mode"] == "course_copy":
        what = f"Copy {source['summary']} from {source['name']}"
    else:
        what = f"Import the cartridge {source['name']} ({source['summary']})"
    if dest.get("new"):
        return (f"Create an unpublished course shell named {dest['name']}"
                f"{' in account ' + str(dest['account_id']) if dest.get('account_id') else ''}, then "
                f"{what[0].lower() + what[1:]} into it. The source is not changed; the new shell stays unpublished.")
    return (f"{what} into {dest['name']}. Nothing in {dest['name']} is deleted; "
            "publish state is not changed.")


def import_course(app, dest_course_id=None, source_course_id=None, imscc: str | None = None,
                  new_course: dict | None = None, force: bool = False, apply: bool = False,
                  gate: Gate | None = None, log: Log = quiet_log, timeout_s: int = 3600,
                  ledger_course_id=None) -> dict:
    """Plan, and with apply=True run the migration: gate, create the shell if
    asked, start the copy or the cartridge upload, wait, report planned vs
    actual counts. `ledger_course_id` is the course whose ledger gets the line
    (the route's course); it defaults to the destination."""
    plan = import_plan(app, dest_course_id, source_course_id, imscc, new_course, force, log)
    if not apply:
        plan["applied"] = False
        return plan
    if plan["refusal"]:
        raise Refused(plan["refusal"])
    if gate is None:
        raise ValueError("an import that writes needs a gate")
    c = app.content
    source, dest = plan["source"], plan["destination"]
    payload = {"source": source.get("id") or source.get("name"), "mode": source["mode"],
               "dest": dest.get("id") or {"new": dest["name"], "account_id": dest.get("account_id")},
               "force": bool(force)}
    gate("courseops.import", payload, plan["sentence"], plan["detail"])

    # -- the shell
    dest_id = dest.get("id")
    if dest.get("new"):
        log(f"creating the unpublished shell {dest['name']} in account {dest['account_id']}")
        try:
            created = c.create_course(dest["account_id"], dest["name"], dest.get("course_code") or dest["name"])
        except CanvasError as exc:
            if exc.status == 403:
                raise Refused(
                    f"Your Canvas role cannot create courses in account {dest['account_id']} (Canvas said 403). "
                    "Content-only admin roles can read campus courses but cannot provision shells. Ask a full "
                    "admin to create the shell in Canvas, then import into it by its course id.") from None
            raise
        dest_id = created.get("id")
        if not dest_id:
            raise RuntimeError(f"Canvas did not return the new course: {created}")
        dest["id"] = str(dest_id)
        log(f"created unpublished shell {dest_id}: {created.get('name')}")

    # -- the migration
    if source["mode"] == "course_copy":
        mig = c.start_course_copy(dest_id, source["id"])
    else:
        log(f"uploading {source['name']} ({round(source['bytes'] / 1024)} KB)")
        mig = c.start_cartridge_import(dest_id, Path(source["path"]))
    mig_id = (mig or {}).get("id")
    if not mig_id:
        raise RuntimeError(f"Canvas did not start the migration: {mig}")
    log(f"migration {mig_id} started ({(mig or {}).get('workflow_state', 'queued')})")
    info = c.wait_migration(dest_id, mig_id, timeout_s=timeout_s, progress=lambda m, d, t: log(m, d, t))
    issues = []
    try:
        issues = [{"kind": i.get("issue_type"), "text": i.get("description")}
                  for i in c.migration_issues(dest_id, mig_id)]
    except Exception:  # noqa: BLE001
        pass
    if info.get("workflow_state") != "completed":
        raise RuntimeError(f"The import failed (migration {mig_id}; {len(issues)} issues). "
                           "Open the course's Import Content page in Canvas for details.")

    log("counting what landed")
    actual = c.course_counts(dest_id)
    entry = {"at": now_iso(), "mode": source["mode"], "source": source.get("id") or source.get("name"),
             "source_name": source["name"], "dest": str(dest_id), "dest_name": dest["name"],
             "new_shell": bool(dest.get("new")), "migration_id": mig_id,
             "planned": plan["planned"], "before": dest["counts"], "actual": actual,
             "issues": issues[:20], "url": f"{app.cfg.base_url}/courses/{dest_id}"}
    ledger_cid = ledger_course_id or dest_id
    append_history(area_dir(app, ledger_cid) / IMPORTS_HISTORY, entry)
    if dest.get("new"):
        sentence = (f"Cloned {source['name']} into a new unpublished shell, {dest['name']} (course {dest_id}); "
                    f"it now holds {_counts_summary(actual)}")
    elif source["mode"] == "course_copy":
        sentence = f"Copied {source['summary']} from {source['name']} into {dest['name']}; nothing was deleted"
    else:
        sentence = f"Imported the cartridge {source['name']} into {dest['name']}; nothing was deleted"
    ledger.record(app.course_dir(ledger_cid), AREA, sentence, url=entry["url"],
                  count=sum(as_int(actual.get(k)) - as_int(dest["counts"].get(k)) for k in GATE_KINDS),
                  kind="import")
    log(f"IMPORT COMPLETE -> course {dest_id}: {_counts_summary(actual)} now present. "
        "Publish state was not touched.")
    return {**plan, "applied": True, "result": entry}


def clone_course(app, course_id, name: str, account_id=None, course_code: str = "",
                 apply: bool = False, gate: Gate | None = None, log: Log = quiet_log) -> dict:
    """This course into a fresh unpublished shell."""
    return import_course(app, None, source_course_id=course_id, imscc=None,
                         new_course={"name": name, "account_id": account_id, "course_code": course_code},
                         force=False, apply=apply, gate=gate, log=log, ledger_course_id=course_id)


def import_history(app, course_id) -> list[dict]:
    rows = load_json(area_dir(app, course_id) / IMPORTS_HISTORY, []) or []
    rows.reverse()
    return rows


def plan_text(plan: dict) -> str:
    s, d = plan["source"], plan["destination"]
    lines = [f"{'COURSE COPY' if s['mode'] == 'course_copy' else 'CARTRIDGE IMPORT'}: {s['name']} -> "
             f"{'NEW shell ' + repr(d['name']) if d.get('new') else d['name']}",
             f"  source holds : {s['summary']}",
             f"  destination  : {'a new unpublished shell' if d.get('new') else d.get('summary')}"]
    if plan.get("refusal"):
        lines.append(f"  REFUSED      : {plan['refusal']}")
    lines.append(f"  would confirm: {plan['sentence']}")
    return "\n".join(lines)


__all__ = ["export_course", "export_history", "exports_dir", "open_folder", "cartridge_counts",
           "import_plan", "import_course", "clone_course", "import_history", "accounts",
           "confirm_sentence", "plan_text", "plural"]
