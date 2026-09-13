"""HTTP routes for the Assistant.

    GET  /api/assistant/{cid}/state              facts, quick jobs, pending, history tail
    POST /api/assistant/{cid}/send   {text}      start or resume the session, send one message
    GET  /api/assistant/{cid}/events?since=N     events after N, plus pending permissions
    POST /api/assistant/{cid}/answer {request_id, decision}
    POST /api/assistant/{cid}/names/refresh      re-read the roster for tagging
    POST /api/assistant/{cid}/stop
    POST /api/assistant/{cid}/new
    POST /api/assistant/permission               the hook's long poll (secret-checked)

The permission route is the one the hook process calls from inside the Claude
session. It carries no Origin header, so the server's same-origin check lets
it through; the per-launch secret is what proves it came from this Studio's
own session and not from another local process.
"""
from __future__ import annotations

from ..routing import HTTPError, route
from .manager import Manager


def install(app) -> None:
    app.assistant = Manager(app)


def _mgr(req) -> Manager:
    mgr = getattr(req.app, "assistant", None)
    if mgr is None:
        raise HTTPError(503, "The Assistant is not installed on this server.")
    return mgr


@route("GET", "/api/assistant/{cid}/state", area="assistant")
def state(req):
    return _mgr(req).state(req.params["cid"])


@route("POST", "/api/assistant/{cid}/send", area="assistant")
def send(req):
    text = (req.body or {}).get("text") if isinstance(req.body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPError(400, "Type something to send first.")
    model = (req.body or {}).get("model") or None
    try:
        return _mgr(req).send(req.params["cid"], text, model=model)
    except PermissionError as exc:
        raise HTTPError(403, str(exc))
    except ValueError as exc:
        raise HTTPError(409, str(exc))
    except RuntimeError as exc:
        raise HTTPError(503, str(exc))


@route("GET", "/api/assistant/{cid}/events", area="assistant")
def events(req):
    try:
        since = int(req.q("since", "0") or 0)
    except ValueError:
        since = 0
    return _mgr(req).events(req.params["cid"], since)


@route("POST", "/api/assistant/{cid}/answer", area="assistant")
def answer(req):
    body = req.body if isinstance(req.body, dict) else {}
    request_id = str(body.get("request_id") or "")
    decision = str(body.get("decision") or "deny")
    if not request_id:
        raise HTTPError(400, "Which request? request_id is missing.")
    try:
        return _mgr(req).answer(request_id, decision)
    except KeyError as exc:
        raise HTTPError(404, str(exc).strip("'\""))


@route("POST", "/api/assistant/{cid}/names/refresh", area="assistant")
def names_refresh(req):
    """Re-read the class list from Canvas so a new student gets a tag.

    A read, so no confirm token. Anyone already tagged keeps their tag: a tag
    that changed meaning between two sessions would make a saved conversation
    say something that is not true.
    """
    names = _mgr(req).names(req.params["cid"], refresh=True)
    return {"enabled": bool(names.enabled and len(names)),
            "students": len(names), "note": names.roster_note()}


@route("POST", "/api/assistant/{cid}/stop", area="assistant")
def stop(req):
    return _mgr(req).stop(req.params["cid"])


@route("POST", "/api/assistant/{cid}/new", area="assistant")
def new(req):
    return _mgr(req).new(req.params["cid"])


@route("POST", "/api/assistant/permission", area="assistant")
def permission(req):
    """Blocks until the person answers or the timer denies. The server is
    threaded, so this holds one worker thread per open question."""
    body = req.body if isinstance(req.body, dict) else {}
    return _mgr(req).permission_request(body)


def hub_status(app, course_id) -> dict:
    mgr = getattr(app, "assistant", None)
    if mgr is None:
        return {"lines": ["The Assistant is not installed."], "badge": None, "needs": [], "actions": []}
    return mgr.hub_status(course_id)
