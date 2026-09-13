"""HTTP routes for the Canvas Inbox.

    GET  /api/inbox/unread                  the header chip's count
    GET  /api/inbox?scope=&course=          the thread list
    GET  /api/inbox/{cid}                   one thread, with its messages
    POST /api/inbox/{cid}/read              what it is asking, and a draft (a job)
    POST /api/inbox/{cid}/reply {body}      send one reply (gated twice)

Reading the inbox is student data, so every call here goes through the
grading-scoped client. Nothing is marked read. Nothing is sent without the
confirm token, and there is no route that sends more than one reply.
"""
from __future__ import annotations

from .. import inbox
from ..routing import HTTPError, route


def install(app) -> None:  # noqa: ARG001  (routes register on import)
    return None


@route("GET", "/api/inbox/unread", area="inbox")
def unread(req):
    return inbox.unread_count(req.app)


@route("GET", "/api/inbox", area="inbox")
def listing(req):
    try:
        limit = max(1, min(int(req.q("limit", "40")), inbox.MAX_THREADS))
    except ValueError:
        limit = 40
    scope = req.q("scope")
    if scope not in ("", "unread", "archived", "sent"):
        raise HTTPError(400, "Canvas knows inbox, unread, archived and sent.")
    return inbox.listing(req.app, scope=scope, course_id=req.q("course") or None,
                         limit=limit)


@route("GET", "/api/inbox/{cid}", area="inbox")
def one(req):
    return inbox.thread(req.app, req.params["cid"])


@route("POST", "/api/inbox/{cid}/read", area="inbox")
def read(req):
    """A job: it calls a model, which takes long enough to need progress."""
    cid = req.params["cid"]
    model = (req.body or {}).get("model") if isinstance(req.body, dict) else None

    def job(log):
        log("Reading the thread. The student's name is not in what goes out.", 0, 2)
        out = inbox.read_thread(req.app, cid, model=model)
        log("Drafted a reply. Nothing has been sent.", 2, 2)
        out["sentence_done"] = "Drafted a reply. Nothing has been sent."
        return out

    return req.job("inbox.read", job)


@route("POST", "/api/inbox/{cid}/reply", area="inbox")
def reply(req):
    body = req.body if isinstance(req.body, dict) else {}
    text = body.get("body")
    if not isinstance(text, str) or not text.strip():
        raise HTTPError(400, "There is nothing in the reply box to send.")
    cid = req.params["cid"]

    def job(log):
        return inbox.send_reply(req.app, cid, text, confirm_token=body.get("confirm"),
                                log=log)

    return req.job("inbox.reply", job)
