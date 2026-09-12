"""Command line entry point.

    python -m courseforge serve       start the local web UI (default)
    python -m courseforge doctor      check Canvas token and Claude login
    python -m courseforge courses     list your courses
"""
from __future__ import annotations

import argparse
import sys

from . import blender, llm
from .canvas import CanvasClient
from .config import Config
from .server import serve


def _indent(text, pad: str = "                 ") -> str:
    """Line up a multi-line message under the label it belongs to."""
    return "\n".join(pad + line if line.strip() else line
                     for line in str(text).splitlines())


def cmd_doctor(cfg: Config) -> int:
    ok = True
    print("CourseForge Studio doctor\n")

    print(f"Canvas host    : {cfg.base_url}")
    token = None
    try:
        token = cfg.token()
        print(f"Canvas token   : found ({len(token)} chars)")
    except Exception as exc:  # noqa: BLE001
        # A token that was never found is not a failed login, and saying so
        # sends people off to regenerate a perfectly good token instead of
        # looking at where they put the file.
        ok = False
        print(f"Canvas token   : NOT FOUND\n{_indent(exc)}")
    if token:
        try:
            me = CanvasClient(cfg.base_url, token).whoami()
            print(f"Canvas login   : OK -- {me.get('name')} (id {me.get('id')})")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"Canvas login   : FAILED\n{_indent(exc)}")
            print("                 The token was read but Canvas rejected it. "
                  "Either the file\n"
                  "                 has something extra in it, or the token was "
                  "revoked;\n"
                  "                 generate a new one under Account -> Settings.")

    print()
    info = llm.doctor()
    print(f"Claude CLI     : {info.get('cli') or 'NOT FOUND'}")
    if info.get("version"):
        print(f"Claude version : {info['version']}")
    if info.get("logged_in"):
        print("Claude login   : OK")
    else:
        ok = False
        print(f"Claude login   : FAILED\n                 {info.get('detail','')}")
        print("\n                 Fix: open a normal terminal, run `claude`, then `/login`.")
        print("                 If you launched this from inside a Claude Code session,")
        print("                 start it from a plain terminal instead.")

    print()
    bl = blender.probe(cfg.blender_path)
    print(f"Blender        : {bl.get('path') or 'NOT FOUND'}")
    if bl.get("version"):
        print(f"Blender version: {bl['version']}")
    if not bl.get("ok"):
        print(f"                 {bl.get('detail', '')}")
        print("                 .blend grading is unavailable until this is fixed.")
        print("                 Install from https://www.blender.org/download/, or set")
        print('                 "blender_path" in config.json.')

    # The optional tools behind the accessibility areas. Missing ones are not a
    # failure: each area disables the verbs that need them and says how to install.
    print()
    from . import tools as _tools
    found = _tools.detect(cfg)
    for name, label in (("tesseract", "Tesseract OCR"), ("verapdf", "veraPDF"),
                        ("java", "Java runtime"), ("pdf_engine", "PDF engine"),
                        ("python_pptx", "python-pptx"), ("python_docx", "python-docx")):
        info = found.get(name, {})
        state = "OK" if info.get("ok") else "not found"
        extra = info.get("version") or info.get("path") or ""
        print(f"{label:15s}: {state}" + (f"  ({extra})" if info.get("ok") and extra else ""))
        if not info.get("ok"):
            print(f"                 enables: {info.get('enables', '')}")
            if info.get("install"):
                print(f"                 install: {info['install']}")

    print()
    try:
        from .server import App
        areas = App(cfg).area_status
        bad = {k: v.get("error") for k, v in areas.items() if not v.get("ok")}
        print(f"Areas          : {', '.join(k for k, v in areas.items() if v.get('ok')) or 'none'}")
        for name, err in bad.items():
            ok = False
            print(f"                 {name}: FAILED to load\n{_indent(err)}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"Areas          : FAILED\n{_indent(exc)}")

    print(f"\nData directory : {cfg.data}")
    print(f"Model          : {cfg.model}  (backend: {llm.backend_name()})")
    print(f"Pseudonymize   : {cfg.pseudonymize}")
    print(f"Canvas writes  : {'allowed, each one confirmed first'
                                 if cfg.allow_canvas_writes else 'LOCKED OFF'}")
    print("\n" + ("All good." if ok else "Some checks failed -- see above."))
    return 0 if ok else 1


def cmd_courses(cfg: Config) -> int:
    client = CanvasClient(cfg.base_url, cfg.token())
    for course in client.courses(cfg.enrollment_types):
        mark = "  (excluded)" if cfg.is_excluded(course) else ""
        term = (course.get("term") or {}).get("name", "")
        print(f"{course['id']:>8}  {course.get('name','')}  [{term}]{mark}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import cli as area_cli
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="courseforge",
        description="CourseForge Studio: grading, accessibility, content and course tools for Canvas. "
                    "With no command it starts the local web UI.")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--port", type=int, help="override the listen port (serve)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    p_serve = sub.add_parser("serve", help="start the local web UI (default)")
    p_serve.add_argument("--port", type=int, dest="serve_port", help="override the listen port")
    sub.add_parser("gui", help="the tkinter status window that owns the server")
    sub.add_parser("doctor", help="check the Canvas token, Claude login and tools")
    sub.add_parser("courses", help="list your courses")
    area_cli.register_all(sub)
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    port = getattr(args, "serve_port", None) or args.port
    if port:
        cfg.port = port

    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "courses":
        return cmd_courses(cfg)
    if args.command == "gui":
        from .launcher import main as gui_main
        return gui_main()
    if args.command in (None, "serve"):
        serve(cfg)
        return 0
    return area_cli.dispatch(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
