"""A second click, enforced by the server rather than by the browser.

Canvas writes used to be gated behind `allow_canvas_writes` in config.json.
That is the wrong shape for the common case: the tool exists to change live
courses, and a config flag that has to be flipped once and then stays on is not
a safety feature after the first day -- it is a setup step that makes the
buttons look broken.

What actually protects a live course is being shown the exact change and having
to agree to it. So every write goes through here twice. The first request comes
back refused, carrying a one-time token and a plain summary of what would
happen. The second request must send that token back.

The token is bound to a fingerprint of the request itself, so:

  * agreeing to one change cannot apply a different one -- edit anything after
    the review screen and the fingerprint moves, so it asks again;
  * a token cannot be replayed, because it is consumed on use;
  * a token cannot sit around, because it expires.

This lives on the server on purpose. A confirm() in the browser is a courtesy;
this is a rule, and it holds for anything that can reach the API -- a stale
page, a replayed fetch, a future button someone forgets to guard.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass


class ConfirmRequired(Exception):
    """Raised on the first attempt. Carries what the caller must show and echo."""

    def __init__(self, kind: str, summary: str, token: str, detail: str = ""):
        super().__init__(summary)
        self.kind = kind
        self.summary = summary
        self.token = token
        self.detail = detail

    def payload(self) -> dict:
        return {
            "needs_confirm": True,
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail,
            "confirm": self.token,
            "error": self.summary,
        }


class ConfirmStale(Exception):
    """The token was unknown, expired, used, or for a different request."""


@dataclass
class _Pending:
    kind: str
    fingerprint: str
    summary: str
    created: float


def fingerprint(kind: str, payload) -> str:
    """A stable hash of what is about to be written.

    Sorted keys so the same request always hashes the same way, and the kind is
    mixed in so a token for one operation cannot be spent on another that
    happens to carry identical arguments.
    """
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{kind}\x00{blob}".encode("utf-8")).hexdigest()


class ConfirmGate:
    """Hands out one-time confirmations and spends them.

    Held in memory only. Restarting the server invalidates every outstanding
    confirmation, which is the safe direction: the worst case is being asked
    again.
    """

    TTL_S = 600          # ten minutes: long enough to read, short enough to forget
    MAX_PENDING = 64     # a bound, so a page that keeps asking cannot grow this

    def __init__(self, ttl_s: int | None = None):
        self.ttl_s = self.TTL_S if ttl_s is None else int(ttl_s)
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    # ------------------------------------------------------------------ api
    def require(self, kind: str, payload, summary: str, token: str | None = None,
                detail: str = "") -> None:
        """Let the write through, or raise.

        Call this immediately before touching Canvas. With no token, it raises
        ConfirmRequired holding one. With a token that matches this exact
        request, it returns and the token is spent.
        """
        want = fingerprint(kind, payload)
        if not token:
            raise ConfirmRequired(kind, summary, self._offer(kind, want, summary),
                                  detail)

        # Popped before it is checked, on purpose: a token that is presented
        # for the wrong change is spent either way. It costs one extra click in
        # the rare case a page sends a slightly different body the second time,
        # and it means a token cannot be probed against several payloads.
        with self._lock:
            self._sweep()
            entry = self._pending.pop(str(token), None)

        if entry is None:
            raise ConfirmStale(
                "That confirmation is no longer valid -- it may have expired, "
                "or already been used. Nothing was sent to Canvas. Look at the "
                "change again and confirm it once more.")
        if entry.kind != kind or entry.fingerprint != want:
            raise ConfirmStale(
                "That confirmation was for a different change, so nothing was "
                "sent to Canvas. This happens when something is edited after "
                "the review screen. Check it again and confirm.")

    def offer(self, kind: str, payload, summary: str) -> str:
        """Mint a confirmation for a change that has just been planned.

        The same thing `require` does when it refuses, but returned instead of
        raised -- for the case where the plan and the review screen are one
        step, so there is nothing to refuse yet. The token still only spends on
        this exact payload.
        """
        return self._offer(kind, fingerprint(kind, payload), summary)

    def pending_count(self) -> int:
        with self._lock:
            self._sweep()
            return len(self._pending)

    # --------------------------------------------------------------- internals
    def _offer(self, kind: str, fp: str, summary: str) -> str:
        token = secrets.token_hex(16)
        with self._lock:
            self._sweep()
            if len(self._pending) >= self.MAX_PENDING:
                # Drop the oldest rather than refuse: the offer is cheap and the
                # only cost of losing one is being asked again.
                oldest = min(self._pending, key=lambda k: self._pending[k].created)
                self._pending.pop(oldest, None)
            self._pending[token] = _Pending(kind, fp, summary, time.time())
        return token

    def _sweep(self) -> None:
        cutoff = time.time() - self.ttl_s
        for key in [k for k, v in self._pending.items() if v.created < cutoff]:
            self._pending.pop(key, None)
