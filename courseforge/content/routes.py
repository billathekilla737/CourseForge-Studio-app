"""Routes for the Build area, under /api/build/{cid}/.

    GET  state                      drafts, manifest summary, last push, needs
    POST draft                      job: Claude drafts {kind, title, module, points, brief, look, source}
    GET  drafts                     every draft (summaries)
    POST drafts                     save human edits {id, title, html, ...}; re-scans
    GET  drafts/{id}                one draft in full
    GET  drafts/{id}/preview        {html} sanitised for the review pane
    POST drafts/{id}/delete
    POST scan                       {html, kind, quiz} -> findings, nothing saved
    POST place                      job: create the object and its module item (gated, ledgered)
    GET  manifest / POST manifest   the saved manifest
    POST manifest/verify            verify_slots + check_style, nothing sent
    POST manifest/push              job: dry-run plan; with apply, gated push
    GET  rubrics / POST rubrics     the saved rubric definitions
    POST rubrics/push               job: dry-run plan; with apply, gated push
    GET  modules                    for the placement picker
    GET  groups                     assignment groups

Every Canvas write runs inside a job, behind req.app._gate, and is recorded
with ledger.record afterwards. Only app.content is used.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .. import htmlclean, ledger
from ..routing import HTTPError, route
from . import check_style, generate, manifest as mf, push_pages, push_project
from . import place as placemod, rubrics as rubricsmod, state as statemod, verify_slots
from . import sync as build_sync
from .common import course_url, publish_word
from .paths import BuildDir

AREA = "build"


def install(app) -> None:
    """Register routes and copy drafts to Canvas user files after a local save."""
    app.build_area = {"installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    from . import paths as build_paths

    def _changed(cid):
        build_sync.push(app, cid)

    build_paths.on_change = _changed


# ---------------------------------------------------------------- helpers
def _build(app, cid) -> BuildDir:
    return BuildDir(app.course_dir(cid), cid)


def _course(app, cid) -> dict:
    for c in app.store.courses() or []:
        if str(c.get("id")) == str(cid):
            return c
    return {}


def _course_name(app, cid, client=None) -> str:
    course = _course(app, cid)
    name = course.get("name")
    if not name and client is not None:
        try:
            name = client.course_detail(cid).get("name")
        except Exception:  # noqa: BLE001
            name = None
    return name or f"course {cid}"


def _course_label(app, cid) -> str:
    course = _course(app, cid)
    return " ".join(x for x in (course.get("course_code"), course.get("name")) if x) or ""


def _palette(app) -> set[str]:
    brand = generate.load_brand(getattr(app.cfg, "brand_path", "") or None)
    return generate.palette_of(brand)


def _draft_summary(d: dict) -> dict:
    findings = d.get("findings") or {}
    return {"id": d.get("id"), "kind": d.get("kind"), "title": d.get("title"),
            "module": d.get("module"), "module_id": d.get("module_id"),
            "position": d.get("position"), "group": d.get("group"),
            "points": (d.get("assignment") or {}).get("points"),
            "publish": d.get("publish"), "look": d.get("look"),
            "scan_ok": findings.get("ok"),
            "scan_failed": len((findings.get("style") or {}).get("failed") or [])
            + len((findings.get("quiz") or {}).get("failed") or []),
            "source": d.get("source") or {}, "summary": d.get("summary"),
            "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
            "placed": d.get("placed"), "chars": len(d.get("html") or ""),
            "questions": len((d.get("quiz") or {}).get("questions") or []) if d.get("kind") == "quiz" else None}


def _detail(rows: list[dict]) -> str:
    """askConfirm parses `detail` as JSON when it is a list of {label, from, to}."""
    return json.dumps(rows[:40])


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - then).total_seconds()
    except ValueError:
        return ""
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(secs // 60)} minutes ago"
    if secs < 129600:
        return f"{int(round(secs / 3600))} hours ago"
    return f"{int(secs // 86400)} days ago"


# ------------------------------------------------------------------ state
@route("GET", "/api/build/{cid}/state", AREA)
def state(req):
    cid = req.params["cid"]
    try:
        sync_info = build_sync.hydrate(req.app, cid) or {}
    except Exception as exc:  # noqa: BLE001
        sync_info = {"did": "error", "reason": f"{type(exc).__name__}: {exc}"}
    build = _build(req.app, cid)
    manifest = build.manifest()
    summary = mf.summary(manifest) if manifest else None
    problems = mf.validate(manifest) if manifest else []
    try:
        st = statemod.load(build)
        state_error = ""
    except ValueError as exc:
        st, state_error = None, str(exc)
    return {
        "course_id": str(cid),
        "course_name": _course_name(req.app, cid),
        "drafts": [_draft_summary(d) for d in build.drafts()],
        "manifest": summary, "manifest_problems": problems,
        "manifest_saved": manifest is not None,
        "rubrics": len(build.rubrics() or []) if isinstance(build.rubrics(), list) else 0,
        "last_push": build.last_push(),
        "state": {"owned": statemod.owns(st, cid), "pages": len((st or {}).get("pages") or {}),
                  "modules": len((st or {}).get("modules") or {}), "error": state_error},
        "kinds": list(generate.KINDS), "looks": list(generate.LOOKS),
        "default_look": getattr(req.app.cfg, "a11y_look", "hybrid") or "hybrid",
        "model": req.app.cfg.model, "models": req.app.cfg.models,
        "needs": [],
        "sync": sync_info,
    }


@route("POST", "/api/build/{cid}/sync", AREA)
def sync_resolve(req):
    """Settle a diverged Build replica. take is local or remote."""
    take = str((req.body or {}).get("take") or "")
    try:
        return build_sync.resolve(req.app, req.params["cid"], take)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPError(503, str(exc)) from None


# ----------------------------------------------------------------- drafts
@route("POST", "/api/build/{cid}/draft", AREA)
def draft(req):
    cid = req.params["cid"]
    body = dict(req.body or {})
    if body.get("kind") not in generate.KINDS:
        raise HTTPError(400, "Pick a kind: page, syllabus, assignment, discussion, quiz or study-guide.")
    if not (body.get("title") or "").strip():
        raise HTTPError(400, "Give the draft a title first.")
    app = req.app
    build = _build(app, cid)
    label = _course_label(app, cid)

    def job(log):
        log(f"drafting the {body['kind']} '{body['title']}'", 0, 2)
        record = generate.draft(app.cfg, build, body, log, course_label=label)
        log("scanned and saved", 2, 2)
        return {"draft": record, "id": record["id"]}
    return req.job("build.draft", job)


@route("GET", "/api/build/{cid}/drafts", AREA)
def drafts(req):
    build = _build(req.app, req.params["cid"])
    return {"drafts": [_draft_summary(d) for d in build.drafts()]}


@route("POST", "/api/build/{cid}/drafts", AREA)
def save_draft(req):
    cid = req.params["cid"]
    body = dict(req.body or {})
    did = body.pop("id", None)
    if not did:
        raise HTTPError(400, "Which draft? The id is missing.")
    build = _build(req.app, cid)
    try:
        record = generate.update_draft(build, did, body, _palette(req.app))
    except FileNotFoundError:
        raise HTTPError(404, "That draft no longer exists.") from None
    return {"draft": record}


@route("GET", "/api/build/{cid}/drafts/{id}", AREA)
def draft_one(req):
    build = _build(req.app, req.params["cid"])
    record = build.draft(req.params["id"])
    if not record:
        raise HTTPError(404, "That draft no longer exists.")
    return {"draft": record}


@route("GET", "/api/build/{cid}/drafts/{id}/preview", AREA)
def draft_preview(req):
    build = _build(req.app, req.params["cid"])
    record = build.draft(req.params["id"])
    if not record:
        raise HTTPError(404, "That draft no longer exists.")
    return {"html": htmlclean.clean(record.get("html") or "", req.app.cfg.base_url),
            "title": record.get("title")}


@route("POST", "/api/build/{cid}/drafts/{id}/delete", AREA)
def draft_delete(req):
    build = _build(req.app, req.params["cid"])
    return {"deleted": build.delete_draft(req.params["id"])}


@route("POST", "/api/build/{cid}/scan", AREA)
def scan(req):
    body = req.body or {}
    probe = {"kind": body.get("kind") or "page", "html": body.get("html") or "",
             "quiz": body.get("quiz") or {}, "assignment": body.get("assignment") or {}}
    return {"findings": generate.scan(probe, _palette(req.app))}


# ------------------------------------------------------------------ place
@route("POST", "/api/build/{cid}/place", AREA)
def place(req):
    cid = req.params["cid"]
    body = dict(req.body or {})
    did = body.get("id")
    if not did:
        raise HTTPError(400, "Which draft? The id is missing.")
    app = req.app
    build = _build(app, cid)
    record = build.draft(did)
    if not record:
        raise HTTPError(404, "That draft no longer exists.")
    if record.get("placed"):
        raise HTTPError(409, f"This draft was already placed on {record['placed'].get('placed_at', '')[:10]}. "
                             "Draft it again to place a second copy.")
    failures = generate.hard_failures(record)
    if failures:
        raise HTTPError(409, "The draft still fails the style check and cannot be placed: " +
                        "; ".join(failures[:3]))
    module_id = body.get("module_id", record.get("module_id")) or None
    position = body.get("position", record.get("position"))
    publish = bool(body.get("publish", record.get("publish", False)))
    group = body.get("group", record.get("group")) or None
    module_name = body.get("module_name") or record.get("module") or ""
    token = req.confirm

    def job(log):
        client = app.content
        name = _course_name(app, cid, client)
        mname = module_name
        if module_id and not mname:
            try:
                mname = next((m.get("name") for m in client.modules(cid) if str(m.get("id")) == str(module_id)), "")
            except Exception:  # noqa: BLE001
                mname = ""
        sentence = placemod.sentence(record, mname if module_id else None, position, publish, name)
        digest = hashlib.sha256((record.get("html") or "").encode("utf-8")).hexdigest()[:16]
        app._gate("build.place",
                  {"course_id": str(cid), "draft": did, "module_id": module_id, "position": position,
                   "publish": publish, "group": group, "html": digest, "title": record.get("title")},
                  sentence, token,
                  detail=_detail([{"label": record.get("title") or "", "from": record.get("kind"),
                                   "to": publish_word(publish)}]),
                  what="placing content in the course")
        result = placemod.place(client, app.cfg.base_url, cid, record, module_id, position, publish,
                                group, log, backup_dir=build.backups_dir)
        record["placed"] = result
        record["publish"] = publish
        record["module_id"] = module_id
        record["module"] = mname or record.get("module")
        record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        build.save_draft(record)
        past = sentence.replace("Create the", "Created the", 1).replace("Replace the", "Replaced the", 1)
        past = past.split(". ")[0] + "."
        ledger.record(app.course_dir(cid), AREA, past, url=result.get("html_url"), count=1,
                      kind="place")
        if result.get("mismatches"):
            log("read back: " + "; ".join(result["mismatches"]))
        return {"placed": result, "draft_id": did, "sentence": past}
    return req.job("build.place", job)


# --------------------------------------------------------------- manifest
@route("GET", "/api/build/{cid}/manifest", AREA)
def manifest_get(req):
    build = _build(req.app, req.params["cid"])
    manifest = build.manifest()
    if manifest is None:
        return {"manifest": None, "summary": None, "problems": [], "root": str(build.root),
                "path": str(build.manifest_path)}
    root = mf.resolve_root(manifest, build.root)
    return {"manifest": manifest, "summary": mf.summary(manifest),
            "problems": mf.validate(manifest, root), "root": str(root), "path": str(build.manifest_path)}


@route("POST", "/api/build/{cid}/manifest", AREA)
def manifest_save(req):
    build = _build(req.app, req.params["cid"])
    body = req.body or {}
    manifest = body.get("manifest")
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except json.JSONDecodeError as exc:
            raise HTTPError(400, f"The manifest is not valid JSON: {exc.msg} at line {exc.lineno}.") from None
    if not isinstance(manifest, dict):
        raise HTTPError(400, "The manifest must be a JSON object.")
    build.save_manifest(manifest)
    root = mf.resolve_root(manifest, build.root)
    return {"summary": mf.summary(manifest), "problems": mf.validate(manifest, root),
            "root": str(root), "path": str(build.manifest_path)}


@route("POST", "/api/build/{cid}/manifest/verify", AREA)
def manifest_verify(req):
    build = _build(req.app, req.params["cid"])
    manifest = (req.body or {}).get("manifest") or build.manifest()
    if not isinstance(manifest, dict):
        raise HTTPError(400, "Save a manifest first.")
    root = mf.resolve_root(manifest, build.root)
    return verify_slots.verify(manifest, root, palette=_palette(req.app))


@route("POST", "/api/build/{cid}/manifest/push", AREA)
def manifest_push(req):
    cid = req.params["cid"]
    app = req.app
    build = _build(app, cid)
    body = dict(req.body or {})
    manifest = body.get("manifest") or build.manifest()
    if not isinstance(manifest, dict):
        raise HTTPError(400, "Save a manifest first.")
    mode = body.get("mode") if body.get("mode") in ("pages", "project") else mf.detect_mode(manifest)
    publish = bool(body.get("publish", False))
    rebuild = bool(body.get("rebuild_modules", False))
    skip_modules = bool(body.get("skip_modules", False))
    do_apply = bool(body.get("apply", False))
    token = req.confirm
    root = mf.resolve_root(manifest, build.root)
    problems = mf.validate(manifest, root)
    if problems and do_apply:
        raise HTTPError(400, "The manifest has problems: " + " ".join(problems[:3]))

    def job(log):
        client = app.content
        log("checking the manifest", 0, 3)
        st = statemod.load(build)
        verify = verify_slots.verify(manifest, root, palette=_palette(app))
        log("reading the course", 1, 3)
        if mode == "pages":
            plan = push_pages.plan(client, cid, manifest, root, st, publish)
        else:
            plan = push_project.plan(client, cid, manifest, root, st, publish, skip_modules, rebuild)
        plan["problems"] = problems
        plan["verify"] = {"ok": verify["ok"], "summary": verify["summary"],
                          "failing": [r for r in verify["rows"] if r["state"] != "ok"][:40],
                          "structural": verify["structural"]}
        plan["course_name"] = _course_name(app, cid, client)
        log("plan ready", 3, 3)
        if not do_apply:
            return {"plan": plan, "applied": False}
        if not verify["ok"]:
            raise ValueError("Verify fails, so nothing was pushed: " + verify["summary"])
        if mode == "project" and (plan.get("gate") or {}).get("refused"):
            raise push_project.ModuleWipeRefused(plan["gate"]["sentence"])
        name = plan["course_name"]
        sentence = plan["sentence"].rstrip(".") + f" in {name}."
        rows = plan.get("pages") if mode == "pages" else plan.get("rows")
        detail = _detail([{"label": r.get("title") or r.get("key") or "", "from": r.get("action"),
                           "to": publish_word(publish)} for r in (rows or []) if r.get("action") != "skip"])
        app._gate("build.push",
                  {"course_id": str(cid), "mode": mode, "publish": publish, "rebuild_modules": rebuild,
                   "skip_modules": skip_modules, "keys": plan.get("keys") or []},
                  sentence, token, detail=detail, what="pushing course content")

        def save_state(s):
            statemod.save(build, s)
        if mode == "pages":
            result = push_pages.apply(client, cid, manifest, root, st, publish, log, on_state=save_state)
            ledger.record(app.course_dir(cid), AREA,
                          f"Pushed the pages manifest: created {result['created']} and updated "
                          f"{result['updated']} {publish_word(publish)} page(s) in {name}.",
                          url=course_url(app.cfg.base_url, cid, "/modules"), count=result["count"], kind="push")
        else:
            result = push_project.apply(client, cid, manifest, root, st, publish, log, skip_modules,
                                        rebuild, on_state=save_state, backup_dir=build.backups_dir)
            ledger.record(app.course_dir(cid), AREA,
                          f"Pushed the project manifest: {len(result['pages'])} page(s), "
                          f"{len(result['assignments'])} assignment(s), {len(result['discussions'])} "
                          f"discussion(s), {len(result['quizzes'])} quiz(zes), {len(result['modules'])} "
                          f"module(s), {publish_word(publish)}, in {name}.",
                          url=course_url(app.cfg.base_url, cid, "/modules"), count=result["count"], kind="push")
        statemod.save(build, st)
        summary = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode,
                   "publish": publish, "count": result.get("count"),
                   "mismatches": result.get("mismatches") or []}
        build.save_last_push(summary)
        if result.get("mismatches"):
            log("read back: " + "; ".join(result["mismatches"][:6]))
        return {"plan": plan, "result": result, "applied": True,
                "review_url": course_url(app.cfg.base_url, cid, "/modules")}
    return req.job("build.push", job)


# ---------------------------------------------------------------- rubrics
@route("GET", "/api/build/{cid}/rubrics", AREA)
def rubrics_get(req):
    build = _build(req.app, req.params["cid"])
    entries = build.rubrics()
    return {"rubrics": entries if isinstance(entries, list) else [], "path": str(build.rubrics_path)}


@route("POST", "/api/build/{cid}/rubrics", AREA)
def rubrics_save(req):
    build = _build(req.app, req.params["cid"])
    entries = (req.body or {}).get("rubrics")
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except json.JSONDecodeError as exc:
            raise HTTPError(400, f"The rubric file is not valid JSON: {exc.msg} at line {exc.lineno}.") from None
    if isinstance(entries, dict):
        entries = entries.get("rubrics") or [entries]
    if not isinstance(entries, list):
        raise HTTPError(400, "Rubrics are a JSON array of definitions.")
    build.save_rubrics(entries)
    return {"count": len(entries)}


@route("POST", "/api/build/{cid}/rubrics/push", AREA)
def rubrics_push(req):
    cid = req.params["cid"]
    app = req.app
    build = _build(app, cid)
    body = dict(req.body or {})
    entries = body.get("rubrics") or build.rubrics()
    if not isinstance(entries, list) or not entries:
        raise HTTPError(400, "Save at least one rubric definition first.")
    do_apply = bool(body.get("apply", False))
    token = req.confirm

    def job(log):
        client = app.content
        log("reading the course's assignments and rubrics", 0, 2)
        plan = rubricsmod.plan(client, cid, entries)
        plan["course_name"] = _course_name(app, cid, client)
        log("plan ready", 2, 2)
        if not do_apply:
            return {"plan": plan, "applied": False}
        if not plan["keys"]:
            raise ValueError("Every rubric was skipped, so there is nothing to push: " +
                             "; ".join(w for r in plan["rows"] for w in r["warnings"][:1]))
        sentence = plan["sentence"].rstrip(".") + f" in {plan['course_name']}."
        # Bound to the definitions themselves, not their titles: a token minted
        # for one set of criteria must not spend on another with the same names.
        app._gate("build.rubrics",
                  {"course_id": str(cid), "titles": plan["keys"], "entries": entries},
                  sentence, token,
                  detail=_detail([{"label": r["title"], "from": r["action"], "to": r["assignment"]}
                                  for r in plan["rows"] if r["action"] != "skip"]),
                  what="pushing rubrics")
        result = rubricsmod.apply(client, cid, entries, log)
        ledger.record(app.course_dir(cid), AREA,
                      f"Pushed {result['count']} rubric definition(s) and attached them to their "
                      f"assignments in {plan['course_name']}.",
                      url=course_url(app.cfg.base_url, cid, "/rubrics"), count=result["count"], kind="rubrics")
        return {"plan": plan, "result": result, "applied": True}
    return req.job("build.rubrics", job)


# --------------------------------------------------------- course lookups
@route("GET", "/api/build/{cid}/modules", AREA)
def modules(req):
    cid = req.params["cid"]
    rows = []
    for m in req.app.content.modules(cid, include_items=True):
        rows.append({"id": m.get("id"), "name": m.get("name"), "position": m.get("position"),
                     "published": m.get("published"),
                     "items": [{"id": it.get("id"), "title": it.get("title"), "type": it.get("type"),
                                "position": it.get("position"), "published": it.get("published")}
                               for it in (m.get("items") or [])],
                     "items_count": m.get("items_count", len(m.get("items") or []))})
    return {"modules": rows}


@route("GET", "/api/build/{cid}/groups", AREA)
def groups(req):
    cid = req.params["cid"]
    return {"groups": [{"id": g.get("id"), "name": g.get("name"), "weight": g.get("group_weight")}
                       for g in req.app.content.assignment_groups(cid)]}


# -------------------------------------------------------------------- hub
def hub_status(app, course_id) -> dict:
    """Local state only; the hub paints in milliseconds and never calls Canvas."""
    build = BuildDir(app.course_dir(course_id), course_id)
    drafts = build.drafts()
    placed = sum(1 for d in drafts if d.get("placed"))
    waiting = [d for d in drafts if not d.get("placed")]
    lines: list[str] = []
    if drafts:
        lines.append(f"{len(drafts)} draft{'s' if len(drafts) != 1 else ''}, {placed} placed")
    manifest = build.manifest()
    if manifest:
        s = mf.summary(manifest)
        bits = [f"{s['pages']} pages"]
        if s["assignments"]:
            bits.append(f"{s['assignments']} assignments")
        if s["quizzes"]:
            bits.append(f"{s['quizzes']} quizzes")
        lines.append(f"manifest: {', '.join(bits)}")
    last = build.last_push()
    if last:
        lines.append(f"pushed {_ago(last.get('at'))}, {publish_word(bool(last.get('publish')))}"
                     + (", read-back mismatches" if last.get("mismatches") else ""))
    if not lines:
        lines = ["Draft a page, assignment, discussion, quiz or study guide with Claude. "
                 "Nothing reaches Canvas until you place it."]
    return {"lines": lines, "badge": len(waiting) or None, "needs": [],
            "actions": [{"label": "New draft", "href": f"#/c/{course_id}/build/new/page"},
                        {"label": "Manifest", "href": f"#/c/{course_id}/build/manifest"}]}
