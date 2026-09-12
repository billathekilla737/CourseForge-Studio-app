"""HTTP routes for the PDF fixer: `/api/pdf/{cid}/...`.

    GET  state                 everything the screen draws, from local files
    GET  picture?hash=         the PNG one figure's description is written from
    POST list                  refresh the Canvas file list              (job)
    POST fetch                 download the originals, six at a time     (job)
    POST fix                   fetch, repair in parallel, collect the alt work (job)
    POST describe              real descriptions from Claude             (job)
    POST alt   {alt:{hash:text}}   a person's descriptions, applied and re-verified (job)
    POST prove                 veraPDF census against PDF/UA-1           (job)
    POST backup                download the originals and change nothing (job)
    POST push  {file_ids?, apply, confirm}   dry run, then upload        (job)
    POST rollback {file_ids, apply, confirm} put the originals back      (job)
    POST queue {dirs, handled}  mark queue entries handled
    POST cancel                stop the running job for this course

Only `push` and `rollback` reach Canvas with a write, and both go through
`req.app._gate` before the first byte and into the ledger afterwards. Every
Canvas call uses `app.content`, which cannot reach student data.
"""
from __future__ import annotations

import threading

from .. import ledger
from ..routing import FileResponse, HTTPError, route
from . import course_pdfs as core

PREFIX = "/api/pdf/{cid}"
AREA = "pdf"

_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()


def install(app) -> None:
    """Routes register at import time; the busy set is all there is to hang."""
    app.pdf = {"busy": _BUSY}


def hub_status(app, course_id) -> dict:
    return core.hub_status(app, course_id)


# ------------------------------------------------------------------ helpers

def _claim(cid: str) -> None:
    with _BUSY_LOCK:
        if cid in _BUSY:
            raise HTTPError(409, "Another PDF job is still running for this course. "
                                 "Wait for it to finish, or press Stop, then try again.")
        _BUSY.add(cid)


def _release(cid: str) -> None:
    with _BUSY_LOCK:
        _BUSY.discard(cid)


def _busy(cid: str) -> bool:
    with _BUSY_LOCK:
        return cid in _BUSY


def _locked_job(req, cid: str, kind: str, fn):
    """A job that holds this course's PDF folder while it runs, so a fetch and
    a push cannot interleave on the same files."""
    _claim(cid)
    core.clear_cancel(cid)

    def job(log):
        try:
            return fn(log)
        except core.Cancelled as exc:
            log(str(exc))
            return {"cancelled": True, "state": _state(req.app, cid)}
        finally:
            _release(cid)
    return req.job(kind, job)


def _state(app, cid) -> dict:
    st = core.state(app, cid)
    st["busy"] = _busy(str(cid))
    return st


def _ids(body) -> list | None:
    ids = (body or {}).get("file_ids")
    if ids in (None, "", []):
        return None
    if not isinstance(ids, list):
        ids = [ids]
    return [str(x) for x in ids]


def _engine_ready(app) -> None:
    missing = [n for n in core.needs(app) if n in ("pymupdf", "pikepdf", "pypdf")]
    if missing:
        raise HTTPError(400, "Install %s first; the PDF fixer cannot read a PDF "
                             "without it." % ", ".join(missing), needs=missing)


# -------------------------------------------------------------------- state

@route("GET", PREFIX + "/state", AREA)
def state(req):
    return _state(req.app, req.params["cid"])


@route("GET", PREFIX + "/picture", AREA)
def picture(req):
    cid = req.params["cid"]
    digest = req.q("hash")
    if not digest:
        raise HTTPError(400, "picture needs ?hash=")
    path = core.picture_for(req.app, cid, digest)
    if path is None or not path.is_file():
        raise HTTPError(404, "No picture is available for that figure.")
    return FileResponse(path, "image/png")


# --------------------------------------------------------- read-only jobs

@route("POST", PREFIX + "/list", AREA)
def list_(req):
    cid = req.params["cid"]

    def job(log):
        out = core.list_files(req.app, cid, log)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.list", job)


@route("POST", PREFIX + "/fetch", AREA)
def fetch(req):
    cid = req.params["cid"]
    ids = _ids(req.body)

    def job(log):
        out = core.fetch(req.app, cid, log, file_ids=ids)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.fetch", job)


@route("POST", PREFIX + "/backup", AREA)
def backup(req):
    cid = req.params["cid"]

    def job(log):
        out = core.backup_only(req.app, cid, log)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.backup", job)


@route("POST", PREFIX + "/fix", AREA)
def fix(req):
    cid = req.params["cid"]
    _engine_ready(req.app)
    body = req.body or {}
    force = bool(body.get("force"))
    jobs = body.get("jobs")
    ids = _ids(body)

    def job(log):
        out = core.fix(req.app, cid, log, jobs=jobs, force=force, file_ids=ids)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.fix", job)


@route("POST", PREFIX + "/describe", AREA)
def describe(req):
    cid = req.params["cid"]
    _engine_ready(req.app)

    def job(log):
        out = core.describe(req.app, cid, log)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.describe", job)


@route("POST", PREFIX + "/alt", AREA)
def alt(req):
    cid = req.params["cid"]
    _engine_ready(req.app)
    incoming = (req.body or {}).get("alt")
    if not isinstance(incoming, dict) or not incoming:
        raise HTTPError(400, "Send the descriptions as {\"alt\": {\"<image hash>\": \"...\"}}.")

    def job(log):
        out = core.save_alt(req.app, cid, incoming, log)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.alt", job)


@route("POST", PREFIX + "/prove", AREA)
def prove(req):
    cid = req.params["cid"]
    profile = (req.body or {}).get("profile")
    if profile and str(profile) not in core.PROFILES:
        raise HTTPError(400, "Unknown profile %r. Choose ua1 or wtpdf." % (profile,))

    def job(log):
        out = core.prove(req.app, cid, log, profile=profile)
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.prove", job)


@route("POST", PREFIX + "/queue", AREA)
def queue(req):
    cid = req.params["cid"]
    body = req.body or {}
    dirs = body.get("dirs") or ([body["dir"]] if body.get("dir") else [])
    core.mark_handled(req.app, cid, dirs, bool(body.get("handled", True)))
    return _state(req.app, cid)


@route("POST", PREFIX + "/cancel", AREA)
def cancel(req):
    cid = req.params["cid"]
    core.request_cancel(cid)
    return {"cancelled": True, "busy": _busy(str(cid)),
            "message": "Stopping. Files already finished keep their repairs; "
                       "nothing was sent to Canvas."}


# ------------------------------------------------------------------- write

@route("POST", PREFIX + "/push", AREA)
def push(req):
    cid = req.params["cid"]
    _engine_ready(req.app)
    body = req.body or {}
    ids = _ids(body)
    apply = bool(body.get("apply"))
    token = req.confirm

    def job(log):
        def gate(payload, sentence, detail):
            req.app._gate("pdf.push", payload, sentence, token, detail=detail,
                          what="uploading fixed PDFs")
        out = core.push(req.app, cid, ids, apply=apply, gate=gate if apply else None, log=log)
        if not apply:
            log("dry run: %d fixed PDF(s) would be uploaded, %d would not. "
                "Nothing was sent." % (out["count"], len(out["held"])))
            out["state"] = _state(req.app, cid)
            return out
        if out.get("uploaded"):
            n = len(out["uploaded"])
            ledger.record(req.app.course_dir(cid), AREA, out["sentence_done"],
                          url="%s/courses/%s/files" % (core.base_url(req.app), cid),
                          count=n, kind="pdf.push",
                          undo={"route": "/pdf/%s/rollback" % cid,
                                "body": {"file_ids": [u["file_id"] for u in out["uploaded"]],
                                         "apply": True}})
        log("done: %d uploaded, %d failed. The originals are kept on this computer."
            % (len(out.get("uploaded") or []), len(out.get("failed") or [])))
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.push", job)


@route("POST", PREFIX + "/rollback", AREA)
def rollback(req):
    cid = req.params["cid"]
    body = req.body or {}
    ids = _ids(body)
    apply = bool(body.get("apply"))
    token = req.confirm

    def job(log):
        def gate(payload, sentence, detail):
            req.app._gate("pdf.rollback", payload, sentence, token, detail=detail,
                          what="restoring the original PDFs")
        out = core.rollback(req.app, cid, ids, apply=apply,
                            gate=gate if apply else None, log=log)
        if not apply:
            log("dry run: %d original(s) would be put back. Nothing was sent."
                % out["count"])
            out["state"] = _state(req.app, cid)
            return out
        if out.get("restored"):
            n = len(out["restored"])
            ledger.record(req.app.course_dir(cid), AREA,
                          "Put %s back over the fixed %s in %s"
                          % (core._plural(n, "original PDF"),
                             "copy" if n == 1 else "copies",
                             core.course_name(req.app, cid)),
                          url="%s/courses/%s/files" % (core.base_url(req.app), cid),
                          count=n, kind="pdf.rollback")
        log("done: %d restored, %d failed" % (len(out.get("restored") or []),
                                              len(out.get("failed") or [])))
        out["state"] = _state(req.app, cid)
        return out
    return _locked_job(req, cid, "pdf.rollback", job)
