"""Routes for the per-student page."""
from __future__ import annotations

from .. import statesync
from ..routing import HTTPError, route
from . import assemble

AREA = "student"
MAX_NOTE = 8000


def install(app) -> None:  # noqa: ARG001
    return None


@route("GET", "/api/students", area=AREA)
def find(req):
    """This term's master list, narrowed when a query is present."""
    term = req.q("term")
    return assemble.search(req.app, req.q("q") or "", term=term or None)


@route("GET", "/api/student/{uid}", area=AREA)
def show(req):
    return assemble.dossier(req.app, req.params["uid"], req.q("course"))


@route("POST", "/api/student/{uid}/live", area=AREA)
def live(req):
    uid = req.params["uid"]

    def job(log):
        return assemble.live(req.app, uid, log)

    return req.job("student.live", job)


@route("POST", "/api/student/{uid}/notes", area=AREA)
def save_notes(req):
    body = req.body if isinstance(req.body, dict) else {}
    text = str(body.get("notes") or "")
    if len(text) > MAX_NOTE:
        raise HTTPError(400, "That note is longer than this page will keep.")
    uid = str(req.params["uid"])
    sync = getattr(req.app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        raise HTTPError(
            503,
            "Canvas state sync is not running, so the note would only live on this computer.",
        )
    saved = assemble._sync_payload(req.app, uid)
    payload = {
        "user_id": uid,
        "notes": text,
        "history": saved.get("history") or [],
    }
    try:
        return sync.put(f"students/{uid}.json", payload)
    except statesync.Conflict:
        raise HTTPError(
            409,
            "Canvas has a newer copy of this note. Reload the page, then save again.",
        ) from None
