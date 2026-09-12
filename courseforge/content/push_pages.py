"""Idempotent page upload from a pages-only manifest (Push-CanvasPages.ps1).

The state file maps each manifest key to the page Canvas created (slug,
page_id, title), the module ids by name, and the module item ids, so a re-run
updates in place instead of creating duplicates.

Rules kept from the script:
- an update PUTs by the STORED slug and then trusts the `url` in the response,
  because editing a title regenerates the slug (Gotcha 7);
- module items go as JSON through `add_module_item` (Gotcha 8);
- the publish state defaults to unpublished and applies to every page and
  module this run touches (Gotcha 10);
- nothing is written unless `apply` is called; `plan` is the dry run.

    plan = push_pages.plan(client, cid, manifest, root, state, publish=False)
    result = push_pages.apply(client, cid, manifest, root, state, publish, log, on_state=save)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import manifest as mf
from .common import quiet_log, same_text, visible_text


def _pages(manifest: dict) -> list[dict]:
    return [p for p in manifest.get("pages") or [] if isinstance(p, dict)]


def _module_names(manifest: dict) -> list[tuple[str, int | None]]:
    seen: dict[str, int | None] = {}
    for p in _pages(manifest):
        name = p.get("module")
        if name and name not in seen:
            seen[name] = p.get("module_position")
    return list(seen.items())


def plan(client, course_id, manifest: dict, root: Path, state: dict, publish: bool = False) -> dict:
    """What `apply` would do, with nothing sent. Reads the module list once."""
    existing = {}
    try:
        for m in client.modules(course_id):
            existing[(m.get("name") or "").strip()] = m.get("id")
    except Exception as exc:  # noqa: BLE001
        existing = {}
        module_error = str(exc)
    else:
        module_error = ""

    rows: list[dict] = []
    for p in _pages(manifest):
        key = mf.page_key(p)
        known = (state.get("pages") or {}).get(key) or {}
        path = mf.resolve_file(p, root)
        file_ok = p.get("html") is not None or (path is not None and path.is_file())
        if not file_ok:
            action, reason = "skip", f"missing file: {path}"
        elif known.get("url"):
            action, reason = "update", f"stored slug /{known['url']}"
        else:
            action, reason = "create", "new page"
        rows.append({"key": key, "title": p.get("title") or "", "module": p.get("module") or "",
                     "module_position": p.get("module_position"), "position": p.get("position"),
                     "action": action, "reason": reason, "slug": known.get("url"),
                     "file": p.get("file") or ("inline" if p.get("html") is not None else "")})
    modules = []
    for name, pos in _module_names(manifest):
        if name in (state.get("modules") or {}):
            action = "reuse"
        elif name in existing:
            action = "reuse"
        else:
            action = "create"
        modules.append({"name": name, "position": pos, "action": action,
                        "id": (state.get("modules") or {}).get(name) or existing.get(name)})
    counts = {"create": sum(r["action"] == "create" for r in rows),
              "update": sum(r["action"] == "update" for r in rows),
              "skip": sum(r["action"] == "skip" for r in rows),
              "modules_new": sum(m["action"] == "create" for m in modules)}
    sentence = (f"Create {counts['create']} and update {counts['update']} page(s), "
                f"{'published' if publish else 'unpublished'}, and place them in "
                f"{len(modules)} module(s) ({counts['modules_new']} new). Existing module items "
                "are not moved or removed.")
    return {"mode": "pages", "publish": publish, "pages": rows, "modules": modules,
            "counts": counts, "sentence": sentence, "module_error": module_error,
            "keys": sorted(r["key"] for r in rows if r["action"] != "skip")}


def apply(client, course_id, manifest: dict, root: Path, state: dict, publish: bool,
          log: Callable | None = None, on_state: Callable[[dict], None] | None = None) -> dict:
    """Push every page, then place it. `on_state(state)` is called after each
    page so a crash midway loses nothing already created."""
    log = quiet_log(log)
    save = on_state or (lambda s: None)
    state.setdefault("pages", {})
    state.setdefault("modules", {})
    state.setdefault("items", {})
    pages = _pages(manifest)
    created = updated = skipped = 0
    mismatches: list[str] = []
    placed: list[dict] = []

    existing_modules: dict[str, int] = {}
    for m in client.modules(course_id):
        existing_modules[(m.get("name") or "").strip()] = m.get("id")

    def module_id(name: str, position) -> int:
        if name in state["modules"]:
            return state["modules"][name]
        if name in existing_modules:
            state["modules"][name] = existing_modules[name]
            return existing_modules[name]
        mod = client.create_module(course_id, name, position=position, published=publish)
        state["modules"][name] = mod["id"]
        existing_modules[name] = mod["id"]
        log(f"created module '{name}'")
        return mod["id"]

    for i, p in enumerate(pages, 1):
        title = p.get("title") or ""
        key = mf.page_key(p)
        log(f"page '{title}'", i - 1, len(pages))
        try:
            body = mf.body_of(p, root)
        except FileNotFoundError:
            log(f"skipped '{title}': no body file yet")
            skipped += 1
            continue
        known = state["pages"].get(key) or {}
        if known.get("url"):
            resp = client.update_page(course_id, known["url"], body=body, title=title, published=publish)
            url = resp.get("url") or known["url"]
            page_id = resp.get("page_id") or known.get("page_id")
            updated += 1
            log(f"updated page '{title}' (/{url})")
        else:
            resp = client.create_page(course_id, title, body, published=publish)
            url, page_id = resp.get("url"), resp.get("page_id")
            created += 1
            log(f"created page '{title}' (/{url})")
        state["pages"][key] = {"url": url, "page_id": page_id, "title": title}

        # Read back: Canvas answers 200 to writes it ignored.
        try:
            back = client.page(course_id, url)
            if (back.get("title") or "") != title:
                mismatches.append(f"'{title}': Canvas kept the title '{back.get('title')}'")
            elif not same_text(back.get("body") or "", body):
                mismatches.append(f"'{title}': the body read back differs from what was sent")
        except Exception as exc:  # noqa: BLE001
            mismatches.append(f"'{title}': could not read the page back ({exc})")

        if p.get("module"):
            mid = module_id(p["module"], p.get("module_position"))
            item_key = f"{mid}::{url}"
            if item_key not in state["items"]:
                item = {"type": "Page", "page_url": url, "title": title}
                if p.get("position") is not None:
                    item["position"] = p["position"]
                if publish is not None:
                    item["published"] = bool(publish)
                added = client.add_module_item(course_id, mid, item)
                state["items"][item_key] = added.get("id")
                placed.append({"title": title, "module": p["module"], "item_id": added.get("id")})
                log(f"placed '{title}' in module '{p['module']}'")
        save(state)

    log("done", len(pages), len(pages))
    return {"created": created, "updated": updated, "skipped": skipped,
            "placed": placed, "mismatches": mismatches, "publish": publish,
            "count": created + updated}


def visible(html: str) -> str:
    return visible_text(html)
