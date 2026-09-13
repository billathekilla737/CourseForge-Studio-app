"""HTTP routes for the Documents area.

    /api/a11y/{cid}/<kind>/state                 GET   items with state, counts, needs
    /api/a11y/{cid}/<kind>/list                  POST  refresh the file list      (job)
    /api/a11y/{cid}/<kind>/fetch                 POST  {file_ids?, pattern?}      (job) download + scan
    /api/a11y/{cid}/<kind>/describe              POST  {file_ids?}                (job) alt text via Claude
    /api/a11y/{cid}/<kind>/fixes                 POST  {file_id, fixes}           save a person's edits
    /api/a11y/{cid}/<kind>/push                  POST  {file_ids?, apply, confirm} (job) dry run / upload
    /api/a11y/{cid}/<kind>/restore               POST  {file_ids?, confirm}       (job) originals back
    /api/a11y/{cid}/<kind>/pattern               POST  {pattern}                  (job) rescan with a regex
    /api/a11y/{cid}/<kind>/picture?file=&hash=   GET   PNG for the alt grid
    /api/a11y/{cid}/<kind>/file/{fid}/report     GET   everything the review pane needs

kind is one of pptx, docx, pdf-text, office-text, triage. Two jobs, not five
variations on one: pptx, docx and triage make a file usable by a screen
reader; pdf-text and office-text change the words inside a file and have
nothing to do with accessibility. The UI groups them under those two names.
Each kind gets its
own literal route so `/api/a11y/{cid}/html/...` (the HTML gateway, another
area) is never shadowed by a `{kind}` wildcard.
"""
from __future__ import annotations

from pathlib import Path

from .. import ledger
from ..routing import FileResponse, HTTPError, route
from . import gateway

PREFIX = "/api/a11y/{cid}"


def install(app) -> None:
    """Routes register at import time; nothing else to hang on the app."""
    app.docs_kinds = list(gateway.KINDS)


def hub_status(app, course_id) -> dict:
    return gateway.hub_status(app, course_id)


def _ids(body) -> list | None:
    """Which files a verb acts on, or None for all of them.

    The shared gateway component sends the ticked rows as `keys`, and for these
    kinds a row's key is its Canvas file id. Read both names: without `keys`,
    ticking three files and pressing Upload would quietly act on all twenty.
    """
    body = body or {}
    ids = body.get("file_ids")
    if ids in (None, "", []):
        ids = body.get("keys")
    if ids in (None, "", []):
        return None
    if not isinstance(ids, list):
        ids = [ids]
    return [str(x) for x in ids]


def _register(kind: gateway.Kind) -> None:
    base = f"{PREFIX}/{kind.id}"

    @route("GET", f"{base}/state", area="docs")
    def state(req):
        return gateway.state(req.app, req.params["cid"], kind)

    @route("POST", f"{base}/list", area="docs")
    def list_(req):
        cid = req.params["cid"]

        def job(log):
            gateway.list_files(req.app, cid, kind, log)
            return gateway.state(req.app, cid, kind)
        return req.job(f"docs.{kind.id}.list", job)

    @route("POST", f"{base}/fetch", area="docs")
    def fetch(req):
        cid = req.params["cid"]
        ids = _ids(req.body)
        pattern = req.body.get("pattern") if kind.needs_pattern else None
        needs = gateway.tool_needs(req.app, kind)
        if needs:
            raise HTTPError(400, f"Install {', '.join(needs)} first; the {kind.label} scanner needs it.",
                            needs=needs)

        def job(log):
            out = gateway.fetch(req.app, cid, kind, ids, pattern, log)
            out["state"] = gateway.state(req.app, cid, kind)
            return out
        return req.job(f"docs.{kind.id}.fetch", job)

    @route("POST", f"{base}/pattern", area="docs")
    def pattern(req):
        cid = req.params["cid"]
        if not kind.needs_pattern:
            raise HTTPError(400, f"{kind.label} does not scan with a pattern.")
        pat = (req.body.get("pattern") or "").strip()
        try:
            import re
            re.compile(pat)
        except re.error as exc:
            raise HTTPError(400, f"That is not a valid pattern: {exc}") from None

        def job(log):
            out = gateway.set_pattern(req.app, cid, kind, pat, log)
            out["state"] = gateway.state(req.app, cid, kind)
            return out
        return req.job(f"docs.{kind.id}.pattern", job)

    @route("POST", f"{base}/describe", area="docs")
    def describe(req):
        cid = req.params["cid"]
        if not kind.has_alt:
            raise HTTPError(400, f"{kind.label} has no pictures to describe.")
        ids = _ids(req.body)

        def job(log):
            from . import describe as _describe
            out = _describe.describe(req.app, cid, kind, ids, log)
            out["state"] = gateway.state(req.app, cid, kind)
            return out
        return req.job(f"docs.{kind.id}.describe", job)

    @route("POST", f"{base}/fixes", area="docs")
    def fixes(req):
        cid = req.params["cid"]
        fid = req.body.get("file_id")
        if fid in (None, ""):
            raise HTTPError(400, "Say which file the fixes belong to (file_id).")
        if kind.report_only:
            raise HTTPError(400, f"{kind.label} is report-only; there is nothing to save.")
        try:
            return gateway.save_fixes(req.app, cid, kind, str(fid), req.body.get("fixes") or {})
        except FileNotFoundError as exc:
            raise HTTPError(404, str(exc)) from None

    @route("POST", f"{base}/push", area="docs")
    def push(req):
        cid = req.params["cid"]
        if kind.report_only:
            raise HTTPError(400, f"{kind.label} only reports; nothing is ever uploaded from it.")
        ids = _ids(req.body)
        apply = bool(req.body.get("apply"))
        token = req.confirm
        options = req.body.get("options") or {}

        def job(log):
            def gate(payload, sentence, detail):
                req.app._gate("docs.push", payload, sentence, token, detail=detail,
                              what=f"uploading fixed {kind.plural}")
            out = gateway.push(req.app, cid, kind, ids, apply=apply,
                               gate=gate if apply else None, options=options, log=log)
            if apply and out.get("uploaded"):
                n = len(out["uploaded"])
                ledger.record(req.app.course_dir(cid), "docs", out["sentence_done"],
                              url=f"{req.app.cfg.base_url}/courses/{cid}/files", count=n,
                              kind=kind.id,
                              undo={"route": f"/a11y/{cid}/{kind.id}/restore",
                                    "body": {"file_ids": [u["file_id"] for u in out["uploaded"]]}})
            out["state"] = gateway.state(req.app, cid, kind)
            return out
        return req.job(f"docs.{kind.id}.push", job)

    @route("POST", f"{base}/restore", area="docs")
    def restore(req):
        cid = req.params["cid"]
        if kind.report_only:
            raise HTTPError(400, f"{kind.label} never uploaded anything, so there is nothing to restore.")
        ids = _ids(req.body)
        token = req.confirm

        def job(log):
            def gate(payload, sentence, detail):
                req.app._gate("docs.restore", payload, sentence, token, detail=detail,
                              what=f"restoring original {kind.plural}")
            out = gateway.restore(req.app, cid, kind, ids, gate=gate, log=log)
            if out.get("restored"):
                n = len(out["restored"])
                ledger.record(req.app.course_dir(cid), "docs",
                              f"Put {n} original {kind.plural if n != 1 else kind.singular} back "
                              f"over the fixed {'copies' if n != 1 else 'copy'} in "
                              f"{gateway.course_name(req.app, cid)}",
                              url=f"{req.app.cfg.base_url}/courses/{cid}/files", count=n, kind=kind.id)
            out["state"] = gateway.state(req.app, cid, kind)
            return out
        return req.job(f"docs.{kind.id}.restore", job)

    @route("GET", f"{base}/picture", area="docs")
    def picture(req):
        cid = req.params["cid"]
        fid = req.q("file")
        h = req.q("hash")
        key = req.q("key")
        if not fid or not (h or key):
            raise HTTPError(400, "picture needs ?file= and ?hash= (or ?key=)")
        it = next((i for i in gateway.items(req.app, cid, kind, [fid])), None)
        if it is None or not it.report:
            raise HTTPError(404, "no scan for that file")
        image = next((im for im in it.report.get("images") or []
                      if (h and im.get("hash") == h) or (key and im.get("key") == key)), None)
        if image is None:
            raise HTTPError(404, "no such picture in that file")
        from . import describe as _describe
        path = _describe.picture_for_browser(it.dir, image)
        if path is None or not Path(path).is_file():
            raise HTTPError(404, "no picture available for that image")
        ext = Path(path).suffix.lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/png")
        return FileResponse(Path(path), ctype)

    @route("GET", f"{base}/file/{{fid}}/report", area="docs")
    def report(req):
        cid = req.params["cid"]
        try:
            return gateway.report_for(req.app, cid, kind, req.params["fid"])
        except FileNotFoundError as exc:
            raise HTTPError(404, str(exc)) from None

    if kind.report_only:
        @route("GET", f"{base}/report", area="docs")
        def triage_report(req):
            cid = req.params["cid"]
            return {"rows": gateway.write_triage(req.app, cid, kind),
                    "classes": _class_help()}


def _class_help() -> dict:
    from . import pdf_triage
    return pdf_triage.CLASS_HELP


for _kind in gateway.KINDS.values():
    _register(_kind)
