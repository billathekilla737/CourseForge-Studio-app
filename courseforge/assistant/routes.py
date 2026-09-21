"""HTTP routes for the Assistant.

    GET  /api/assistant/{cid}/state              facts, quick jobs, pending, history tail
    POST /api/assistant/{cid}/send   {text}      start or resume the session, send one message
    GET  /api/assistant/{cid}/events?since=N     events after N, plus pending permissions
    POST /api/assistant/{cid}/answer {request_id, decision}
    GET  /api/assistant/{cid}/roster             the names the @ picker offers
    POST /api/assistant/{cid}/names/refresh      re-read the roster for tagging
    POST /api/assistant/{cid}/stop
    POST /api/assistant/{cid}/new
    POST /api/assistant/{cid}/mode {mode}        plan | ask | auto
    POST /api/assistant/permission               the hook's long poll (secret-checked)

The permission route is the one the hook process calls from inside the Claude
session. It carries no Origin header, so the server's same-origin check lets
it through; the per-launch secret is what proves it came from this Studio's
own session and not from another local process.
"""
from __future__ import annotations

from ..routing import HTTPError, route
from . import sync as asst_sync
from .manager import Manager, NameProblem


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
    allow_near = bool((req.body or {}).get("allow_near"))
    try:
        return _mgr(req).send(req.params["cid"], text, model=model,
                              allow_near=allow_near)
    except PermissionError as exc:
        raise HTTPError(403, str(exc))
    except NameProblem as exc:
        # The choices ride along, so the composer can offer the name it thinks
        # was meant instead of only telling the person they got it wrong.
        raise HTTPError(409, str(exc), **exc.view())
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


@route("GET", "/api/assistant/{cid}/roster", area="assistant")
def roster(req):
    """The names the @ picker offers. Local only: this never reaches a model.

    It is the same list the grading screens already show on this machine, and
    the reason it is worth having in the composer is that picking a name is
    the one way to be certain it is spelled the way the roster spells it.
    """
    names = _mgr(req).names(req.params["cid"])
    return {"enabled": bool(names.enabled and len(names)),
            "students": names.roster()}


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


@route("POST", "/api/assistant/{cid}/sync", area="assistant")
def sync_resolve(req):
    """Settle a diverged Assistant replica. take is local or remote."""
    take = str((req.body or {}).get("take") or "")
    try:
        return asst_sync.resolve(req.app, req.params["cid"], take)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPError(503, str(exc)) from None


@route("POST", "/api/assistant/{cid}/mode", area="assistant")
@route("POST", "/api/assistant/{cid}/prefs", area="assistant")
def mode(req):
    """Plan/ask/auto and the Claude model. Does not change Canvas."""
    body = req.body if isinstance(req.body, dict) else {}
    try:
        return _mgr(req).set_prefs(
            req.params["cid"],
            mode=body.get("mode") if "mode" in body else None,
            model=body.get("model") if "model" in body else None)
    except ValueError as exc:
        raise HTTPError(400, str(exc))


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
