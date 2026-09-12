"""Command-line verbs for the areas.

The web UI calls area functions directly. The same functions are reachable
from a terminal, and from the Assistant's Claude Code session, as
`python -m courseforge <area> <verb> ...`. Each area may ship a `cli.py` with:

    def register(sub: argparse._SubParsersAction) -> None   # add its parser(s)
    def run(args, cfg) -> int                                # execute

The dry-run rule holds on the command line too: a verb that writes to Canvas
does nothing without `--apply`, and with `--apply` it asks for a typed `yes`
unless it is running inside a gated Assistant session (`CF_STUDIO_GATED=1`),
where the person already clicked Allow on that exact command.
"""
from __future__ import annotations

import importlib
import os
import sys

from .areas import AREAS

_MODULES: dict[str, object] = {}


def register_all(sub) -> None:
    for name in AREAS:
        try:
            mod = importlib.import_module(f"courseforge.{name}.cli")
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.endswith(f"{name}.cli"):
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[courseforge] {name} CLI unavailable: {exc}", file=sys.stderr)
            continue
        if hasattr(mod, "register"):
            mod.register(sub)
            _MODULES[name] = mod


def dispatch(args, cfg) -> int:
    area = getattr(args, "area", None) or (args.command or "").split("-")[0]
    mod = _MODULES.get(area)
    if mod is None or not hasattr(mod, "run"):
        print(f"unknown command {args.command!r}", file=sys.stderr)
        return 2
    return int(mod.run(args, cfg) or 0)


def confirm_apply(sentence: str) -> bool:
    """The second click, on the command line.

    Inside a gated Assistant session the Allow click was the confirmation, so
    the environment marker stands in for the typed yes. Anywhere else a person
    has to type it; a non-interactive stdin is a no.
    """
    if os.environ.get("CF_STUDIO_GATED") == "1":
        return True
    print(sentence)
    try:
        answer = input("Type yes to apply this to Canvas: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"
