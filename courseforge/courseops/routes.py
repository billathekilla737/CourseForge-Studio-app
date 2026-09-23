"""Routes for the Course tools area, under /api/tools/{cid}/.

    GET  state                  everything the area's screens show, from disk
    POST export {type}          job: Canvas packs the course, we download it
    GET  exports                the export history, and whether each file is still there
    POST exports/open           show the folder in the file manager (local only)
    POST import/plan {...}      job: counts on both sides, and the sentence
    POST import {..., apply}    job: the migration, gated and ledgered
    POST clone {name, ...}      job: this course into a new unpublished shell
    GET  nav                    the trim plan against the keep-list
    POST nav {keep, apply}      job: hide, show and reorder tabs, gated
    GET  dates/plan?tz=         the week table, computed from what Canvas knows
    POST dates/plan {...}       the same, with the facts a person typed in
    POST dates/apply {rows}     job: write one due date per week, gated
    GET  quizzes                the quiz list, backups marked
    POST quiz-backup {quiz_id}  job: an unpublished copy of one quiz, gated
    POST slo/resolve            job: course code -> candidate programs
    POST slo/fetch              job: framework PDF, outcomes, course inventory
    POST slo/align              job: Claude judges, the validator checks
    GET  slo/state              the four steps, from disk
    POST slo/report {apply}     job: the report page, unpublished, gated

Every Canvas write runs inside a job, stops at `req.app._gate` before the first
request, and lands in the ledger afterwards. Only `app.content` is used, so
nothing here can reach student work. The modules do the work and take a `gate`
callable; this file is what turns the page's confirm token into one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..routing import HTTPError, route
from . import calendar as cal
from . import due_dates, export_import, nav, quiz_backup, slo
from .common import AREA, Refused, area_dir, course_label, load_json

PREFIX = "/api/tools/{cid}"


@route("GET", "/api/term-calendar", area=AREA)
def term_calendar(req):
    """Campus breaks for the term on the schedule page.

    Read from the college academic calendar. Nothing is written to a course.
    """
    from . import schoolcal
    term = req.q("term")
    if not term:
        try:
            term = req.app.default_term()
        except Exception:  # noqa: BLE001
            term = ""
    return schoolcal.for_term(req.app, term)


def install(app) -> None:
    """Importing this module registered the routes; the App only needs to know
    the area is here."""
    app.courseops = {"installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ------------------------------------------------------------------ helpers
def _course(app, cid) -> dict:
    for c in app.store.courses() or []:
        if str(c.get("id")) == str(cid):
            return c
    return {}


def _label(app, cid) -> str:
    """The course name without calling Canvas, for the screens that only paint."""
    return course_label(_course(app, cid), cid)


def _detail(rows) -> str:
    """askConfirm renders `detail` as a from/to list when it parses as JSON."""
    if not rows:
        return ""
    if isinstance(rows, str):
        return rows
    return json.dumps(list(rows)[:60], default=str)


def _gate_for(req, what: str):
    """The gate the modules call, wired to this request's confirm token."""
    def gate(kind, payload, sentence, detail=None):
        req.app._gate(kind, payload, sentence, req.confirm, detail=_detail(detail), what=what)
    return gate


def _tz(req) -> tuple[str | None, int | None]:
    """The instructor's timezone, as the page reports it: an IANA name where
    the browser knows one, and the JavaScript offset in minutes."""
    body = req.body if isinstance(req.body, dict) else {}
    name = body.get("tzname") or body.get("tz_name") or req.q("tzname") or None
    raw = body.get("tz", body.get("tz_offset_min", req.q("tz")))
    try:
        offset = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        offset = None
    return (str(name) if name else None), offset


def _refuse(exc: Refused) -> HTTPError:
    return HTTPError(409, str(exc))


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    secs = max(0, int((datetime.now(timezone.utc) - when).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} minutes ago"
    if secs < 129600:
        return f"{round(secs / 3600)} hours ago"
    return f"{secs // 86400} days ago"


# -------------------------------------------------------------------- state
@route("GET", PREFIX + "/state", AREA)
def state(req):
    """One read of what is on disk. No Canvas call: this is the screen the
    person lands on and it has to paint at once."""
    app, cid = req.app, req.params["cid"]
    folder = area_dir(app, cid)
    exports = export_import.export_history(app, cid)
    imports = export_import.import_history(app, cid)
    dates_plan = due_dates.last_plan(app, cid)
    dates_apply = due_dates.last_apply(app, cid)
    nav_apply = nav.last_apply(app, cid)
    nav_plan = load_json(folder / nav.PLAN_FILE)
    backups = quiz_backup.history(app, cid)
    slo_state_ = slo.state(app, cid)
    return {
        "course_id": str(cid),
        "course_label": _label(app, cid),
        "base_url": getattr(app.cfg, "base_url", "") or "",
        "exports": {"rows": exports[:20], "count": len(exports),
                    "dir": str(export_import.exports_dir(app, cid)),
                    "last": exports[0] if exports else None},
        "imports": {"rows": imports[:20], "count": len(imports),
                    "last": imports[0] if imports else None},
        "dates": {"plan": dates_plan, "last_apply": dates_apply,
                  "weekdays": list(due_dates.WEEKDAYS),
                  "known_terms": cal.known_terms()},
        "nav": {"plan": nav_plan, "last_apply": nav_apply,
                "default_keep": nav.keep_list(app.cfg)},
        "quiz_backups": {"rows": backups[:20], "count": len(backups),
                         "last": backups[0] if backups else None},
        "slo": slo_state_,
        "needs": slo_state_.get("needs") or [],
    }


# ------------------------------------------------------------------- export
@route("POST", PREFIX + "/export", AREA)
def export(req):
    """Canvas packs the course; we download the file and keep it here. A read
    on the Canvas side, so no confirm, but it is recorded like any other
    Canvas operation."""
    app, cid = req.app, req.params["cid"]
    kind = (req.body or {}).get("type") or "common_cartridge"
    if kind in ("imscc", "cartridge"):
        kind = "common_cartridge"
    if kind not in ("common_cartridge", "zip"):
        raise HTTPError(400, "Pick a cartridge (the whole course) or a zip (the files only).")

    def job(log):
        entry = export_import.export_course(app, cid, log=log, export_type=kind)
        return {"export": entry, "history": export_import.export_history(app, cid)[:20]}
    return req.job("courseops.export", job)


@route("GET", PREFIX + "/exports", AREA)
def exports(req):
    app, cid = req.app, req.params["cid"]
    rows = export_import.export_history(app, cid)
    return {"rows": rows, "dir": str(export_import.exports_dir(app, cid)), "count": len(rows)}


@route("POST", PREFIX + "/exports/open", AREA)
def exports_open(req):
    """Show the folder on this machine. Nothing leaves it."""
    app, cid = req.app, req.params["cid"]
    wanted = (req.body or {}).get("path")
    folder = export_import.exports_dir(app, cid)
    if wanted:
        path = Path(wanted)
        target = path if path.is_dir() else path.parent
        if folder.resolve() not in [target.resolve(), *target.resolve().parents]:
            raise HTTPError(400, "That folder is not this course's exports folder.")
        folder = target
    return {"opened": export_import.open_folder(folder), "dir": str(folder)}


# ------------------------------------------------------------ import, clone
def _import_args(body: dict) -> dict:
    source = body.get("source_course") or body.get("source_course_id")
    imscc = body.get("imscc_path") or body.get("imscc")
    new_course = body.get("new_course")
    if isinstance(new_course, dict) and not (new_course.get("name") or "").strip():
        new_course = None
    return {"source_course_id": str(source).strip() if source else None,
            "imscc": str(imscc).strip() if imscc else None,
            "new_course": new_course if isinstance(new_course, dict) else None,
            "force": bool(body.get("force"))}


@route("POST", PREFIX + "/import/plan", AREA)
def import_plan(req):
    """Counts on both sides and the sentence that would be confirmed. Reads
    only; Canvas is not written to."""
    app, cid = req.app, req.params["cid"]
    args = _import_args(req.body or {})
    dest = None if args["new_course"] else ((req.body or {}).get("dest_course") or cid)

    def job(log):
        try:
            plan = export_import.import_plan(
                app, dest, args["source_course_id"], args["imscc"], args["new_course"],
                args["force"], log)
        except (ValueError, FileNotFoundError) as exc:
            raise Refused(str(exc)) from None
        plan["applied"] = False
        log(plan["refusal"] or f"Dry run. {plan['sentence']} Nothing has been sent.")
        return plan
    return req.job("courseops.import.plan", job)


@route("POST", PREFIX + "/import", AREA)
def import_(req):
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    args = _import_args(body)
    dest = None if args["new_course"] else (body.get("dest_course") or cid)
    apply = bool(body.get("apply"))
    gate = _gate_for(req, "importing course content")

    def job(log):
        try:
            return export_import.import_course(
                app, dest, args["source_course_id"], args["imscc"], args["new_course"],
                args["force"], apply=apply, gate=gate, log=log, ledger_course_id=cid)
        except (ValueError, FileNotFoundError) as exc:
            raise Refused(str(exc)) from None
    return req.job("courseops.import", job)


@route("POST", PREFIX + "/clone", AREA)
def clone(req):
    """This course into a fresh, unpublished shell."""
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPError(400, "Give the new shell a name.")
    account = body.get("account_id")
    code = (body.get("course_code") or "").strip()
    apply = bool(body.get("apply"))
    gate = _gate_for(req, "cloning a course")

    def job(log):
        try:
            return export_import.clone_course(app, cid, name, account, code,
                                              apply=apply, gate=gate, log=log)
        except (ValueError, FileNotFoundError) as exc:
            raise Refused(str(exc)) from None
    return req.job("courseops.clone", job)


# ---------------------------------------------------------------------- nav
@route("GET", PREFIX + "/nav", AREA)
def nav_plan(req):
    app, cid = req.app, req.params["cid"]
    keep = req.query.get("keep") or None
    plan = nav.read_plan(app, cid, [k for k in keep if k] if keep else None)
    plan["course_label"] = _label(app, cid)
    plan["sentence"] = nav.sentence_for(plan, plan["course_label"])
    return plan


@route("POST", PREFIX + "/nav", AREA)
def nav_apply(req):
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    keep = body.get("keep")
    keep = [str(k) for k in keep] if isinstance(keep, list) else None
    if not body.get("apply", True):
        plan = nav.read_plan(app, cid, keep)
        plan["applied"] = False
        return plan
    gate = _gate_for(req, "changing the course navigation")

    def job(log):
        return nav.apply(app, cid, keep, gate, log)
    return req.job("courseops.nav", job)


# -------------------------------------------------------------------- dates
@route("GET", PREFIX + "/dates/plan", AREA)
def dates_plan_get(req):
    """The week table from what Canvas already knows. Reads only."""
    app, cid = req.app, req.params["cid"]
    tz_name, tz_offset = _tz(req)
    try:
        return due_dates.plan(app, cid, None, tz_name, tz_offset)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None


@route("POST", PREFIX + "/dates/plan", AREA)
def dates_plan_post(req):
    """The same table, with the facts the person typed in for whatever Canvas
    could not answer."""
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    tz_name, tz_offset = _tz(req)
    overrides = {k: body[k] for k in ("start", "weeks", "term", "finals_end", "breaks",
                                      "weekday", "time") if k in body}
    try:
        return due_dates.plan(app, cid, overrides, tz_name, tz_offset)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None


@route("POST", PREFIX + "/dates/apply", AREA)
def dates_apply(req):
    """Write one due date per week onto the assignments and quizzes in each
    Week N module. Every row can be edited on the page first, so the rows the
    page sends are what is written."""
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPError(400, "There are no weeks to write. Compute the table first.")
    tz_name, tz_offset = _tz(req)
    gate = _gate_for(req, "setting due dates")

    def job(log):
        try:
            return due_dates.apply(app, cid, rows, gate, log, tz_name, tz_offset)
        except ValueError as exc:
            raise Refused(str(exc)) from None
    return req.job("courseops.dates", job)


# ------------------------------------------------------------- quiz backup
@route("GET", PREFIX + "/quizzes", AREA)
def quizzes(req):
    app, cid = req.app, req.params["cid"]
    rows = quiz_backup.quizzes(app, cid)
    return {"quizzes": rows, "history": quiz_backup.history(app, cid)[:20],
            "prefix": quiz_backup.TITLE_PREFIX,
            "base_url": getattr(app.cfg, "base_url", "") or ""}


@route("POST", PREFIX + "/quiz-backup", AREA)
def quiz_backup_(req):
    """A second quiz is created in the course, so this asks first even though
    nothing existing is touched."""
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    quiz_id = body.get("quiz_id")
    if not quiz_id:
        raise HTTPError(400, "Pick the quiz to back up.")
    if not body.get("apply", True):
        try:
            return {**quiz_backup.plan(app, cid, quiz_id), "applied": False}
        except Refused as exc:
            raise _refuse(exc) from None
    gate = _gate_for(req, "creating a backup quiz")

    def job(log):
        return quiz_backup.backup(app, cid, quiz_id, gate, log)
    return req.job("courseops.quiz-backup", job)


# --------------------------------------------------------------------- slo
@route("GET", PREFIX + "/slo/state", AREA)
def slo_state(req):
    return slo.state(req.app, req.params["cid"])


@route("POST", PREFIX + "/slo/resolve", AREA)
def slo_resolve(req):
    app, cid = req.app, req.params["cid"]
    body = req.body or {}
    code = body.get("course_code") or (_course(app, cid) or {}).get("code")
    refresh = bool(body.get("refresh"))

    def job(log):
        nonlocal code
        if not code:
            log("reading the course code from Canvas")
            code = (app.content.course_detail(cid) or {}).get("course_code")
        log(f"looking up {code or 'this course'} in the Mississippi program index")
        found = slo.resolve(code, cip=body.get("cip") or None, name=body.get("name") or None,
                            app=app, course_id=cid, refresh=refresh)
        log(found["conclusion"])
        return found
    return req.job("courseops.slo.resolve", job)


@route("POST", PREFIX + "/slo/fetch", AREA)
def slo_fetch(req):
    app, cid = req.app, req.params["cid"]
    body = req.body or {}

    def job(log):
        return slo.fetch(app, cid, slug=body.get("slug") or None,
                         course_code=body.get("course_code") or None,
                         title=body.get("title") or None,
                         use_prior=bool(body.get("use_prior")), log=log)
    return req.job("courseops.slo.fetch", job)


@route("POST", PREFIX + "/slo/align", AREA)
def slo_align(req):
    app, cid = req.app, req.params["cid"]
    body = req.body or {}

    def job(log):
        out = slo.align(app, cid, log=log, model=body.get("model") or None)
        out["state"] = slo.state(app, cid)
        return out
    return req.job("courseops.slo.align", job)


@route("POST", PREFIX + "/slo/report", AREA)
def slo_report(req):
    """The report is pushed to one fixed page slug, always unpublished, so a
    second run replaces the same page instead of littering the course."""
    app, cid = req.app, req.params["cid"]
    apply = bool((req.body or {}).get("apply"))
    gate = _gate_for(req, "adding the alignment report to the course")

    def job(log):
        out = slo.report(app, cid, apply=apply, gate=gate, log=log)
        out["state"] = slo.state(app, cid)
        return out
    return req.job("courseops.slo.report", job)


# ---------------------------------------------------------------- hub card
def hub_status(app, course_id) -> dict:
    """Local state only: manifests, histories and the ledger. The hub paints in
    tens of milliseconds and never calls Canvas."""
    lines: list[str] = []
    badge = None

    plan = due_dates.last_plan(app, course_id)
    applied = due_dates.last_apply(app, course_id)
    if applied:
        line = f"Due dates: {applied.get('written', 0)} set {_ago(applied.get('at'))}"
        if applied.get("failed"):
            line += f", {applied['failed']} did not take"
        lines.append(line + ".")
    elif plan and plan.get("writes"):
        lines.append(f"Due dates: {plan['writes']} items would move. Nothing is written yet.")
        # Not a badge: this is what a rollover *would* change, not work
        # waiting on anyone, and the area bar summed it into "24 waiting".
        badge = None
    elif plan and (plan.get("facts") or {}).get("missing"):
        lines.append("Due dates: tell me the term start, the number of weeks and the end of "
                     "finals, and the week table is computed from them.")

    exports = export_import.export_history(app, course_id)
    imports = export_import.import_history(app, course_id)
    if exports:
        last = exports[0]
        lines.append(f"Backups: {len(exports)} on this machine, last {_ago(last.get('at'))} "
                     f"({last.get('kb', 0)} KB).")
    if imports:
        last = imports[0]
        lines.append(f"Last import: {last.get('source_name')} into {last.get('dest_name')} "
                     f"{_ago(last.get('at'))}.")

    nav_last = nav.last_apply(app, course_id)
    if nav_last:
        visible = nav_last.get("visible_after_live") or nav_last.get("visible_after") or []
        lines.append("Navigation: " + " > ".join(visible[:6]) + ".")

    backups = quiz_backup.history(app, course_id)
    if backups:
        lines.append(f"Quiz backups: {len(backups)}, last {backups[0].get('title')} "
                     f"{_ago(backups[0].get('at'))}.")

    slo_state_ = slo.state(app, course_id)
    counts = (slo_state_.get("validate") or {}).get("counts") or {}
    if slo_state_.get("report"):
        lines.append(f"Outcomes: {counts.get('coverage', 0)}% assessed, report page in the "
                     "course, unpublished.")
    elif counts.get("outcomes"):
        lines.append(f"Outcomes: {counts.get('assessed', 0)} of {counts['outcomes']} assessed, "
                     f"{counts.get('gaps', 0)} gaps. The report is not in the course yet.")
        badge = badge or (counts.get("gaps") or None)
    elif slo_state_.get("outcomes"):
        lines.append(f"Outcomes: {len(slo_state_['outcomes'])} found in the framework, not "
                     "aligned yet.")

    if not lines:
        lines = ["Due dates, backups, imports, clones, the left-hand navigation, a quiz copy "
                 "and the outcome report. Every one shows the plan first and nothing reaches "
                 "Canvas until you agree to it."]
    return {
        "lines": lines,
        "badge": badge,
        "needs": slo_state_.get("needs") or [],
        "actions": [{"label": "Due dates", "href": f"#/c/{course_id}/tools/dates"},
                    {"label": "Back up the course", "href": f"#/c/{course_id}/tools/export"}],
    }
