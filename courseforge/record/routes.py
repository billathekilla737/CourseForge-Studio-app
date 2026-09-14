"""The record of actions: read it, check it, and keep it in Canvas.

    GET  /api/record/{cid}                  entries, who is named, chain state
    GET  /api/record/{cid}/file?month=      the raw month file, to download
    POST /api/record/{cid}/verify           walk the chain and say where it breaks
    POST /api/record/{cid}/sync             upload the changed months now
    POST /api/record/{cid}/setting          {to_canvas: bool}

`cid` is a Canvas course id, or the word `account` for the things that are not
about one course (the standing accommodation list, which is one list across
every section).

Nothing here writes to a course. The one Canvas write is putting the record
into the instructor's own user files, which is what the record is for, so it
carries no confirm token -- but the screen says plainly where it goes, and one
switch stops it.
"""
from __future__ import annotations

from pathlib import Path

from .. import audit
from ..routing import FileResponse, HTTPError, route

ACCOUNT = "account"


def install(app) -> None:  # noqa: ARG001  (routes register on import)
    return None


def _root(app, cid: str) -> Path:
    if str(cid) == ACCOUNT:
        return Path(app.store.root)
    return Path(app.course_dir(cid))


def _to_canvas(app) -> bool:
    return bool(getattr(app.cfg, "audit_to_canvas", True))


@route("GET", "/api/record/{cid}", area="record")
def read(req):
    cid = req.params["cid"]
    root = _root(req.app, cid)
    try:
        limit = max(1, min(int(req.q("limit", "200")), 2000))
    except ValueError:
        limit = 200
    rows = audit.read(root, limit=limit, month=req.q("month"),
                      student=req.q("student"), area=req.q("area"))
    out = audit.summary(root)
    return {
        "course_id": cid,
        "rows": rows,
        "students": audit.students_seen(root),
        "folder": str(audit.folder(root)),
        "canvas_folder": audit.FOLDER,
        "to_canvas": _to_canvas(req.app),
        **out,
    }


@route("GET", "/api/record/{cid}/file", area="record")
def download(req):
    """The month file itself, byte for byte. A record nobody can get a copy of
    is not much of a record: this is what gets attached to an email."""
    cid = req.params["cid"]
    root = _root(req.app, cid)
    month = req.q("month")
    # Months come back oldest first. With no month asked for, the button says
    # "this month", so the newest file is the one to hand over.
    for path in reversed(audit.months(root)):
        if not month or path.stem == month:
            return FileResponse(path, "application/x-ndjson", download=True)
    raise HTTPError(404, "Nothing has been recorded for that month.")


@route("POST", "/api/record/{cid}/verify", area="record")
def verify(req):
    return audit.verify(_root(req.app, req.params["cid"]))


@route("POST", "/api/record/{cid}/sync", area="record")
def sync(req):
    cid = req.params["cid"]
    root = _root(req.app, cid)

    def job(log):
        log("Saving the record to your Canvas files", 0, 2)
        out = audit.sync(req.app.client, root,
                         None if str(cid) == ACCOUNT else cid,
                         force=bool((req.body or {}).get("force")),
                         say=lambda t: log(t, 1, 2))
        log(out["detail"], 2, 2)
        return {**out, "sentence_done": out["detail"], **audit.summary(root)}

    return req.job("record.sync", job)


@route("POST", "/api/record/{cid}/setting", area="record")
def setting(req):
    """Turn the Canvas copy on or off, and remember it.

    Off does not stop the record: the local chain is written either way. It
    stops the copy leaving this machine, which is the choice someone on a
    borrowed or shared account should be able to make in one click.
    """
    body = req.body if isinstance(req.body, dict) else {}
    if "to_canvas" not in body:
        raise HTTPError(400, "Say what to change: to_canvas is missing.")
    req.app.cfg.audit_to_canvas = bool(body["to_canvas"])
    try:
        req.app.cfg.persist(audit_to_canvas=bool(body["to_canvas"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPError(500, "Changed for this run, but config.json could not be "
                             "written, so it goes back on restart: %s" % exc)
    return {"to_canvas": req.app.cfg.audit_to_canvas}


def hub_status(app, course_id) -> dict:
    """One line on the course hub, and the badge when nothing has been saved."""
    root = _root(app, course_id)
    info = audit.summary(root)
    n = info["entries"]
    if not n:
        return {"lines": ["Nothing recorded in this course yet. Every Canvas "
                          "change the Studio makes is written down here."],
                "badge": None, "needs": [],
                "actions": [{"label": "Open the record", "href": f"#/c/{course_id}/record"}]}
    unsaved = [m for m in info["months"]
               if audit.upload_name(course_id, m) not in (info["uploaded"] or {})]
    lines = [f"{n} action{'s' if n != 1 else ''} recorded"
             + (f", last {info['last'].get('at', '')[:10]}" if info.get("last") else "")]
    lines.append("Kept in your Canvas files as well." if not unsaved and _to_canvas(app)
                 else "Not yet copied to Canvas." if _to_canvas(app)
                 else "The Canvas copy is turned off, so this is on this PC only.")
    return {"lines": lines, "badge": None, "needs": [],
            "actions": [{"label": "Open the record", "href": f"#/c/{course_id}/record"}]}
