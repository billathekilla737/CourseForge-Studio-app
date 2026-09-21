"""The areas of CourseForge Studio, and how they attach to the server.

An area is a subpackage with a `routes.py` exposing `install(app)`. Installing
imports the module (which registers its routes with `routing.ROUTER`) and lets
it hang state off the App. A broken area is reported, not fatal: the grader and
every other area keep working, and /api/health names what failed.
"""
from __future__ import annotations

import importlib
import traceback

# Order matters only for the course hub's card order.
AREAS = ("a11y", "docs", "pdf", "content", "courseops", "assistant")
# Core modules that register routes but are not areas with a hub card.
CORE = ("hub", "record", "inboxarea", "reports", "extendarea", "studentsarea")


def install_all(app) -> dict:
    status: dict[str, dict] = {}
    for name in CORE + AREAS:
        try:
            module = importlib.import_module(f"courseforge.{name}.routes")
            install = getattr(module, "install", None)
            if install:
                install(app)
            status[name] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            status[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    app.area_status = status
    return status
