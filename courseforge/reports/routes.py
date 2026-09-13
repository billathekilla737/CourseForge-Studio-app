"""Two reports a dean asks for, and neither of them writes anything.

    GET  /api/reports/score/{cid}          one course, before and after
    POST /api/reports/score  {course_ids}  several courses (a job)
    GET  /api/reports/policies/{cid}       one syllabus against the policy list
    POST /api/reports/policies {course_ids}  several syllabi (a job)

Both are reads. The score is assembled from files already on this machine; the
policy check reads each syllabus through the content client, which cannot touch
student data. Neither has an --apply form, because the fix for a missing policy
statement is a decision about wording that belongs to a person.
"""
from __future__ import annotations

from .. import policies, score
from ..routing import HTTPError, route


def install(app) -> None:  # noqa: ARG001  (routes register on import)
    return None


def _ids(body) -> list:
    rows = (body or {}).get("course_ids") if isinstance(body, dict) else None
    out = [str(c).strip() for c in (rows or []) if str(c).strip()]
    if not out:
        raise HTTPError(400, "Pick at least one course.")
    return out


@route("GET", "/api/reports/score/{cid}", area="reports")
def score_one(req):
    return score.forecast(req.app, req.params["cid"])


@route("POST", "/api/reports/score", area="reports")
def score_many(req):
    ids = _ids(req.body)

    def job(log):
        rows = []
        for i, cid in enumerate(ids, start=1):
            log("%d/%d scoring" % (i, len(ids)), i - 1, len(ids))
            out = score.forecast(req.app, cid)
            out["course"] = _name(req.app, cid)
            rows.append(out)
            log("%d/%d done" % (i, len(ids)), i, len(ids))
        scored = [r for r in rows if r["before"] is not None]
        before = round(sum(r["before"] for r in scored) / len(scored), 1) if scored else None
        after = round(sum(r["after"] for r in scored) / len(scored), 1) if scored else None
        return {
            "courses": rows, "before": before, "after": after,
            "files": sum(r["files"] for r in rows),
            "sentence_done": (
                "Across %d course%s: %.0f now, %.0f before."
                % (len(scored), "" if len(scored) == 1 else "s", after, before)
                if scored else "Nothing in these courses has been scanned yet."),
            "method": score.forecast(req.app, ids[0])["method"],
        }

    return req.job("reports.score", job)


@route("GET", "/api/reports/policies/{cid}", area="reports")
def policies_one(req):
    return policies.check_course(req.app, req.params["cid"])


@route("POST", "/api/reports/policies", area="reports")
def policies_many(req):
    ids = _ids(req.body)

    def job(log):
        out = policies.check_many(req.app, ids, log=log)
        out["sentence_done"] = out["summary"]
        return out

    return req.job("reports.policies", job)


def _name(app, cid) -> str:
    for c in (app.store.courses() or []):
        if str(c.get("id")) == str(cid):
            return c.get("title") or c.get("name") or str(cid)
    return str(cid)
