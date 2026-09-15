"""A small route table for the areas that joined the grader.

The grader's server dispatches its own routes through two long `if` chains.
That works for one area; five more would not fit. New areas register routes
here with patterns like `/api/a11y/{cid}/status`, and the request handler tries
this table before falling through to the grader's chains, so nothing that
already worked has to move.

A handler receives one `Request` and returns:
- a dict                      -> JSON 200
- a `FileResponse`            -> the file, with Range support, via the handler
- `req.job(kind, fn)`         -> starts a background job, answers {"job": id}
- raise `HTTPError(status, message)` for a client-visible refusal

Every Canvas write inside a job still goes through `req.app._gate(...)`, the
confirm-token second click, exactly as the grader's jobs do.
"""
from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MISS = object()


class HTTPError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


@dataclass
class FileResponse:
    path: Path
    content_type: str = "application/octet-stream"
    download: bool = False


@dataclass
class JobStart:
    kind: str
    fn: Callable


@dataclass
class Request:
    app: Any
    handler: Any
    method: str
    path: str
    params: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)

    # -- conveniences -------------------------------------------------------
    def q(self, name: str, default: str = "") -> str:
        vals = self.query.get(name)
        return vals[0] if vals else default

    def flag(self, name: str) -> bool:
        return self.q(name, "0") in ("1", "true", "yes")

    def job(self, kind: str, fn: Callable) -> JobStart:
        """`fn(log)` runs on a worker thread; the response is {"job": id}."""
        return JobStart(kind, fn)

    @property
    def confirm(self) -> str | None:
        return self.body.get("confirm") if isinstance(self.body, dict) else None


@dataclass
class Route:
    method: str
    pattern: str
    regex: re.Pattern
    fn: Callable
    area: str


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def add(self, method: str, pattern: str, fn: Callable, area: str = "") -> None:
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern.rstrip("/")) + "/?$")
        self.routes.append(Route(method.upper(), pattern, regex, fn, area))

    def route(self, method: str, pattern: str, area: str = ""):
        def deco(fn):
            self.add(method, pattern, fn, area)
            return fn
        return deco

    def match(self, method: str, path: str) -> tuple[Route, dict] | None:
        for route in self.routes:
            if route.method != method.upper():
                continue
            m = route.regex.match(path)
            if m:
                return route, m.groupdict()
        return None


ROUTER = Router()


def route(method: str, pattern: str, area: str = ""):
    """Module-level decorator: `@route("GET", "/api/pdf/{cid}/files")`."""
    return ROUTER.route(method, pattern, area)


def dispatch(app, handler, method: str, url, query: dict, body: dict) -> bool:
    """Try the table. True when a route answered (the response is already sent)."""
    hit = ROUTER.match(method, url.path)
    if not hit:
        return False
    route_, params = hit
    req = Request(app=app, handler=handler, method=method, path=url.path,
                  params=params, query=query, body=body or {})
    try:
        result = route_.fn(req)
    except HTTPError as exc:
        payload = {"error": exc.message}
        payload.update(exc.extra)
        handler._json(payload, exc.status)
        return True
    except Exception as exc:  # noqa: BLE001
        payload = _error_payload(exc)
        handler._json(payload, payload.pop("_status", 500))
        return True

    if isinstance(result, JobStart):
        job = app.jobs.start(result.kind, result.fn)
        handler._json({"job": job})
    elif isinstance(result, FileResponse):
        handler._send_file(Path(result.path), result.content_type, result.download)
    elif result is None:
        handler._json({"ok": True})
    else:
        handler._json(result)
    return True


def _error_payload(exc: Exception) -> dict:
    """Shape an unexpected exception the way the grader's chains do."""
    name = type(exc).__name__
    # Lazy imports keep this module free of server-side dependencies at import time.
    try:
        from .canvas import CanvasError
        if isinstance(exc, CanvasError):
            return {"error": str(exc), "_status": 502}
    except Exception:  # noqa: BLE001
        pass
    try:
        from .canvas_policy import PolicyDenied
        if isinstance(exc, PolicyDenied):
            return {"error": str(exc), "_status": 403}
    except Exception:  # noqa: BLE001
        pass
    try:
        from .claude_cli import NotLoggedIn
        if isinstance(exc, NotLoggedIn):
            return {"error": str(exc), "needs_login": True, "_status": 401}
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, (ValueError, KeyError, FileNotFoundError)):
        return {"error": f"{name}: {exc}", "_status": 400}
    traceback.print_exception(exc)
    return {"error": name, "_status": 500}
