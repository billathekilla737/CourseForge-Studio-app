"""Build a project or capstone course from a project manifest (Push-CanvasProject.ps1).

Pages upsert by slug, the front page sets `default_view=wiki`, the syllabus
tab is written, assignments upsert by name with submission types and an
auto-created assignment group, graded discussions upsert by title, quizzes are
created unpublished, their old questions deleted, the new ones posted as JSON
in the WRITE shape, and published only if asked. Modules are rebuilt in
manifest order.

The module-wipe safety gate comes first and before any write: when the course
already has modules and no state file proves this tool built them, refuse with
a plain sentence unless the caller passed `rebuild_modules`. `skip_modules`
sidesteps the rebuild and the gate for a content-only pass.

Quiz PUTs carry one concern each (metadata, then description, then dates):
combined they can 400 on this instance (gotcha 12).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import check_quiz, manifest as mf
from .common import num, quiet_log, same_text
from . import state as statemod

NOTE_BOX = ('<div style="padding: 10px 12px; border-radius: 8px; background: #fff8e6; '
            'border-left: 4px solid #E9A821; font-size: 14px; color: #061E3F; margin-bottom: 12px;">'
            '<strong>{label}</strong> {note}</div>')


class ModuleWipeRefused(RuntimeError):
    """The course has modules this tool did not build. Nothing was written."""


def module_gate(client, course_id, state: dict | None, rebuild_modules: bool = False,
                skip_modules: bool = False) -> dict:
    """Decide whether the module rebuild may run. Returns the facts either way;
    raises ModuleWipeRefused when it may not."""
    if skip_modules:
        return {"modules": None, "owned": statemod.owns(state, course_id), "refused": False,
                "skipped": True, "sentence": "Modules are left untouched on this run."}
    existing = list(client.modules(course_id))
    owned = statemod.owns(state, course_id)
    n = len(existing)
    refused = n > 0 and not owned and not rebuild_modules
    if refused:
        sentence = (f"This course already has {n} module{'s' if n != 1 else ''} and there is no "
                    "record that this tool built them. Pushing the manifest would delete that "
                    "module structure and rebuild it from the manifest. Nothing has been written. "
                    "Choose 'Update content, leave modules alone' or turn on 'Rebuild modules' to "
                    "go ahead anyway.")
    elif n == 0:
        sentence = "The course has no modules yet; the manifest's modules will be created."
    elif owned:
        sentence = f"This tool built the {n} existing module{'s' if n != 1 else ''}; they are rebuilt in manifest order."
    else:
        sentence = (f"Rebuild modules is on: the {n} existing module{'s' if n != 1 else ''} will be "
                    "deleted and rebuilt from the manifest. Pages, assignments and quizzes survive; "
                    "only the module list changes.")
    facts = {"modules": n, "owned": owned, "refused": refused, "skipped": False,
             "rebuild_modules": bool(rebuild_modules), "sentence": sentence,
             "names": [m.get("name") for m in existing][:40]}
    if refused:
        exc = ModuleWipeRefused(sentence)
        exc.facts = facts  # type: ignore[attr-defined]
        raise exc
    return facts


def _by(rows, field) -> dict[str, dict]:
    return {(r.get(field) or "").strip(): r for r in rows if isinstance(r, dict)}


def _entries(manifest: dict, key: str) -> list[dict]:
    return [r for r in manifest.get(key) or [] if isinstance(r, dict)]


def _slug(p: dict) -> str:
    return str(p.get("slug") or mf.slugify(p.get("title") or ""))


def plan(client, course_id, manifest: dict, root: Path, state: dict, publish: bool = False,
         skip_modules: bool = False, rebuild_modules: bool = False) -> dict:
    """The dry run. Reads the course's pages, assignments, discussions, quizzes
    and modules to say create vs update per slot; writes nothing."""
    pub = "published" if publish else "unpublished"
    try:
        gate = module_gate(client, course_id, state, rebuild_modules, skip_modules)
    except ModuleWipeRefused as exc:
        gate = exc.facts  # type: ignore[attr-defined]

    existing_pages = {p.get("url"): p for p in client.pages(course_id)}
    existing_a = _by(client.assignments_content(course_id), "name")
    existing_d = _by(client.discussions(course_id), "title")
    existing_q = _by(client.quizzes_content(course_id), "title")

    rows: list[dict] = []
    for p in _entries(manifest, "pages"):
        slug = _slug(p)
        path = mf.resolve_file(p, root)
        ok = p.get("html") is not None or (path is not None and path.is_file())
        rows.append({"kind": "page", "key": slug, "title": p.get("title") or "",
                     "action": "skip" if not ok else ("update" if slug in existing_pages else "create"),
                     "reason": "" if ok else f"missing file: {path}",
                     "front_page": bool(p.get("front_page"))})
    if manifest.get("syllabus_file") or manifest.get("syllabus_html") is not None:
        rows.append({"kind": "syllabus", "key": "syllabus", "title": "Syllabus tab",
                     "action": "update", "reason": "replaces the syllabus body"})
    for a in _entries(manifest, "assignments"):
        name = a.get("name") or ""
        rows.append({"kind": "assignment", "key": a.get("key"), "title": name,
                     "action": "update" if name.strip() in existing_a else "create",
                     "points": a.get("points"), "group": a.get("group"),
                     "reason": ", ".join(a.get("submission_types") or [])})
    for d in _entries(manifest, "discussions"):
        title = d.get("title") or ""
        rows.append({"kind": "discussion", "key": d.get("key"), "title": title,
                     "action": "update" if title.strip() in existing_d else "create",
                     "points": d.get("points"), "reason": "graded discussion" if d.get("points") else "discussion"})
    for q in _entries(manifest, "quizzes"):
        title = q.get("title") or ""
        rep = check_quiz.check(list(q.get("questions") or []))
        rows.append({"kind": "quiz", "key": q.get("key"), "title": title,
                     "action": "update" if title.strip() in existing_q else "create",
                     "points": rep["points"], "questions": rep["count"], "group": q.get("group"),
                     "reason": (f"{rep['count']} questions" if rep["ok"] else "; ".join(rep["failed"][:2])),
                     "ok": rep["ok"]})
    modules = [{"name": m.get("name"), "items": len(m.get("items") or [])} for m in _entries(manifest, "modules")]
    counts = {"create": sum(r["action"] == "create" for r in rows),
              "update": sum(r["action"] == "update" for r in rows),
              "skip": sum(r["action"] == "skip" for r in rows)}
    parts = [f"create {counts['create']} and update {counts['update']} item(s) ({pub})"]
    if skip_modules:
        parts.append("leave the modules alone")
    elif gate.get("modules"):
        parts.append(f"delete the {gate['modules']} existing module(s) and rebuild {len(modules)} from the manifest")
    else:
        parts.append(f"create {len(modules)} module(s)")
    sentence = ("Push the project manifest: " + ", ".join(parts) +
                ". Pages, assignments, discussions and quizzes are never deleted by this push.")
    return {"mode": "project", "publish": publish, "rows": rows, "modules": modules,
            "counts": counts, "gate": gate, "skip_modules": skip_modules,
            "rebuild_modules": rebuild_modules, "sentence": sentence,
            "keys": sorted(str(r.get("key") or r["title"]) for r in rows if r["action"] != "skip")}


def apply(client, course_id, manifest: dict, root: Path, state: dict, publish: bool,
          log: Callable | None = None, skip_modules: bool = False, rebuild_modules: bool = False,
          on_state: Callable[[dict], None] | None = None, backup_dir: Path | None = None) -> dict:
    log = quiet_log(log)
    save = on_state or (lambda s: None)
    for k in ("pages", "assignments", "discussions", "quizzes", "modules"):
        state.setdefault(k, {})
    # The gate, again, right before the first write.
    gate = module_gate(client, course_id, state, rebuild_modules, skip_modules)

    result: dict = {"pages": [], "syllabus": False, "assignments": [], "discussions": [],
                    "quizzes": [], "modules": [], "mismatches": [], "publish": publish, "gate": gate}
    total = (len(_entries(manifest, "pages")) + len(_entries(manifest, "assignments")) +
             len(_entries(manifest, "discussions")) + len(_entries(manifest, "quizzes")) +
             (0 if skip_modules else len(_entries(manifest, "modules"))) + 1)
    step = 0

    # ---- 1) pages (PUT by slug = upsert) -----------------------------------
    front = False
    for p in _entries(manifest, "pages"):
        step += 1
        title = p.get("title") or ""
        slug = _slug(p)
        log(f"page '{title}'", step, total)
        body = mf.body_of(p, root)
        r = client.update_page(course_id, slug, body=body, title=title, published=publish,
                               front_page=True if p.get("front_page") else None)
        url = r.get("url") or slug
        state["pages"][slug] = {"url": url, "page_id": r.get("page_id"), "title": title}
        result["pages"].append({"slug": url, "title": title})
        front = front or bool(p.get("front_page"))
        try:
            back = client.page(course_id, url)
            if not same_text(back.get("body") or "", body):
                result["mismatches"].append(f"page '{title}': body read back differs")
        except Exception as exc:  # noqa: BLE001
            result["mismatches"].append(f"page '{title}': could not read back ({exc})")
        save(state)
    if front:
        client.update_course_content(course_id, default_view="wiki")
        log("front page set as the course landing view")

    # ---- 2) syllabus ------------------------------------------------------
    step += 1
    if manifest.get("syllabus_file") or manifest.get("syllabus_html") is not None:
        log("syllabus tab", step, total)
        body = mf.body_of({"file": manifest.get("syllabus_file"), "html": manifest.get("syllabus_html")}, root)
        if backup_dir is not None:
            try:
                old = client.course_detail(course_id, include=["syllabus_body"]).get("syllabus_body") or ""
                from datetime import datetime
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                Path(backup_dir).mkdir(parents=True, exist_ok=True)
                (Path(backup_dir) / f"syllabus-{stamp}.html").write_text(old, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log(f"could not back up the old syllabus ({exc})")
        client.update_course_content(course_id, syllabus_body=body)
        back = client.course_detail(course_id, include=["syllabus_body"]).get("syllabus_body") or ""
        if not same_text(back, body):
            result["mismatches"].append("syllabus: body read back differs")
        result["syllabus"] = True

    # ---- 3) assignment groups + assignments (upsert by name) ---------------
    groups: dict[str, int] = {}
    for g in client.assignment_groups(course_id):
        groups[(g.get("name") or "").strip().lower()] = g.get("id")

    def group_id(name: str | None):
        if not name:
            return None
        key = name.strip().lower()
        if key not in groups:
            g = client.create_assignment_group(course_id, name)
            groups[key] = g.get("id")
            log(f"created assignment group '{name}'")
        return groups[key]

    existing_a = _by(client.assignments_content(course_id), "name")
    for a in _entries(manifest, "assignments"):
        step += 1
        name = a.get("name") or ""
        log(f"assignment '{name}'", step, total)
        fields = {"name": name, "description": mf.body_of(a, root),
                  "points_possible": num(a.get("points")), "published": publish}
        if a.get("due_at"):
            fields["due_at"] = a["due_at"]
        gid = group_id(a.get("group"))
        if gid:
            fields["assignment_group_id"] = gid
        if a.get("submission_types"):
            fields["submission_types"] = list(a["submission_types"])
        hit = existing_a.get(name.strip())
        r = (client.update_assignment_content(course_id, hit["id"], **fields) if hit
             else client.create_assignment(course_id, **fields))
        state["assignments"][str(a.get("key"))] = r.get("id")
        result["assignments"].append({"key": a.get("key"), "id": r.get("id"), "name": name,
                                      "action": "update" if hit else "create"})
        if num(r.get("points_possible"), -1) != num(a.get("points")):
            result["mismatches"].append(f"assignment '{name}': points read back as {r.get('points_possible')}")
        save(state)

    # ---- 4) graded discussions (upsert by title) ---------------------------
    existing_d = _by(client.discussions(course_id), "title")
    for d in _entries(manifest, "discussions"):
        step += 1
        title = d.get("title") or ""
        log(f"discussion '{title}'", step, total)
        message = mf.body_of(d, root)
        if d.get("note"):
            message = NOTE_BOX.format(label=d.get("note_label") or "This week's milestone:",
                                      note=d["note"]) + message
        assignment = None
        if d.get("points") is not None:
            assignment = {"points_possible": num(d.get("points")), "grading_type": "points",
                          "submission_types": ["discussion_topic"]}
            if d.get("due_at"):
                assignment["due_at"] = d["due_at"]
        hit = existing_d.get(title.strip())
        if hit:
            r = client.update_discussion(course_id, hit["id"], title=title, message=message,
                                         published=publish, assignment=assignment)
        else:
            r = client.create_discussion(course_id, title, message, published=publish,
                                         assignment=assignment)
        state["discussions"][str(d.get("key"))] = r.get("id")
        result["discussions"].append({"key": d.get("key"), "id": r.get("id"), "title": title,
                                      "action": "update" if hit else "create"})
        if assignment and not (r.get("assignment") or {}).get("id"):
            result["mismatches"].append(f"discussion '{title}': Canvas returned no grading assignment")
        save(state)

    # ---- 5) quizzes (created unpublished; questions rebuilt; one concern per PUT)
    existing_q = _by(client.quizzes_content(course_id), "title")
    for q in _entries(manifest, "quizzes"):
        step += 1
        title = q.get("title") or ""
        questions = list(q.get("questions") or [])
        log(f"quiz '{title}' ({len(questions)} questions)", step, total)
        rep = check_quiz.check(questions)
        if not rep["ok"]:
            raise ValueError(f"quiz '{title}' has question problems: " + "; ".join(rep["failed"][:3]))
        meta = {"title": title, "quiz_type": "assignment"}
        if q.get("time_limit"):
            meta["time_limit"] = int(q["time_limit"])
        if q.get("shuffle_answers"):
            meta["shuffle_answers"] = True
        gid = group_id(q.get("group"))
        if gid:
            meta["assignment_group_id"] = gid
        desc = mf.body_of(q, root) if mf.has_body(q) else ""
        hit = existing_q.get(title.strip())
        if hit:
            qid = hit["id"]
            client.update_quiz_content(course_id, qid, published=False)
            client.update_quiz_content(course_id, qid, **meta)
            for old in client.quiz_questions(course_id, qid):
                client.delete_quiz_question(course_id, qid, old["id"])
        else:
            r = client.create_quiz(course_id, published=False, **meta)
            qid = r["id"]
        if desc:
            client.update_quiz_content(course_id, qid, description=desc)
        if q.get("due_at"):
            client.update_quiz_content(course_id, qid, due_at=q["due_at"])
        for n, qq in enumerate(questions, 1):
            client.create_quiz_question(course_id, qid, check_quiz.to_write_shape(qq, n))
        back = list(client.quiz_questions(course_id, qid))
        if len(back) != len(questions):
            result["mismatches"].append(f"quiz '{title}': {len(back)} questions read back, {len(questions)} sent")
        if publish:
            client.update_quiz_content(course_id, qid, published=True)
        state["quizzes"][str(q.get("key"))] = qid
        result["quizzes"].append({"key": q.get("key"), "id": qid, "title": title,
                                  "questions": len(questions), "action": "update" if hit else "create"})
        save(state)

    # ---- 6) modules (rebuild from scratch) ---------------------------------
    if skip_modules:
        log("modules left untouched")
    else:
        for m in client.modules(course_id):
            client.delete_module(course_id, m["id"])
        state["modules"] = {}
        state["module_order"] = []
        log("cleared the existing modules")
        for pos, m in enumerate(_entries(manifest, "modules"), 1):
            step += 1
            name = m.get("name") or f"Module {pos}"
            log(f"module '{name}'", step, total)
            mod = client.create_module(course_id, name, position=pos, published=publish)
            mid = mod["id"]
            placed = 0
            for it in m.get("items") or []:
                t = it.get("type")
                item: dict | None = None
                if t == "Page":
                    entry = state["pages"].get(str(it.get("slug")))
                    if entry:
                        item = {"type": "Page", "page_url": entry["url"]}
                elif t in ("Assignment", "Discussion", "Quiz"):
                    bucket = {"Assignment": "assignments", "Discussion": "discussions", "Quiz": "quizzes"}[t]
                    cid = state[bucket].get(str(it.get("key")))
                    if cid:
                        item = {"type": t, "content_id": int(cid)}
                elif t == "SubHeader":
                    item = {"type": "SubHeader", "title": it.get("title") or ""}
                if item is None:
                    result["mismatches"].append(f"module '{name}': could not resolve {t} {it.get('slug') or it.get('key') or ''}")
                    continue
                if it.get("indent") is not None:
                    item["indent"] = int(it["indent"])
                item["published"] = bool(publish)
                client.add_module_item(course_id, mid, item)
                placed += 1
            state["modules"][name] = mid
            state["module_order"].append(name)
            result["modules"].append({"name": name, "id": mid, "items": placed})
        state["built_modules"] = True
        save(state)

    log("done", total, total)
    result["count"] = (len(result["pages"]) + len(result["assignments"]) + len(result["discussions"])
                       + len(result["quizzes"]) + (1 if result["syllabus"] else 0))
    return result
