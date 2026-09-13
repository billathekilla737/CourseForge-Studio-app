"""Which optional tools this machine has, and how to get the missing ones.

The UI shows an "install X to enable" card for anything missing and disables
the verbs that need it, rather than failing halfway through a job.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _version(cmd: list[str], take: int = 1) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW)
        text = (out.stdout or out.stderr or "").strip().splitlines()
        return text[0][:80] if text else ""
    except Exception:  # noqa: BLE001
        return ""


def java_dirs() -> list[tuple[Path, str]]:
    """Where a Java that is not on PATH actually lives, as (root, glob) pairs.

    Worth looking, because installed-but-not-on-PATH is the normal case rather
    than the odd one: the Temurin installer offers "add to PATH" as a choice, a
    managed install often declines it, and a PATH change never reaches a program
    that is already running. Miss this and the first-run window comes back on a
    machine that already has Java, which is the one thing it must not do.
    """
    return [
        (Path.home() / "tools", "jdk-*/bin/java.exe"),
        (Path(r"C:\Program Files\Eclipse Adoptium"), "*/bin/java.exe"),
        (Path(r"C:\Program Files\Java"), "*/bin/java.exe"),
        (Path(r"C:\Program Files (x86)\Eclipse Adoptium"), "*/bin/java.exe"),
        (Path("/usr/lib/jvm"), "*/bin/java"),
        (Path("/Library/Java/JavaVirtualMachines"), "*/Contents/Home/bin/java"),
    ]


def _first_match(pairs: list[tuple[Path, str]]) -> str:
    """The newest-looking hit across those folders, or an empty string."""
    for root, pattern in pairs:
        try:
            if not root.is_dir():
                continue
            hits = sorted(root.glob(pattern), reverse=True)
        except OSError:
            continue
        if hits:
            return str(hits[0])
    return ""


def _module(name: str, pip: str, enables: str, label: str = "") -> dict:
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "") or getattr(mod, "VersionBind", "") or ""
        return {"ok": True, "label": label or pip, "version": str(ver), "enables": enables}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "label": label or pip, "version": "", "enables": enables,
                "detail": f"{type(exc).__name__}: {exc}",
                "install": f"python -m pip install {pip}"}


def detect(cfg=None) -> dict:
    """Probe everything once. Cheap enough to run on every /api/health."""
    out: dict[str, dict] = {}

    # --- external binaries the PDF engine shells out to -----------------------
    tess = (getattr(cfg, "tesseract_path", "") or os.environ.get("TESSERACT_EXE")
            or shutil.which("tesseract"))
    if not tess:
        for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if Path(cand).is_file():
                tess = cand
                break
    out["tesseract"] = {
        "label": "Tesseract OCR",
        "ok": bool(tess and Path(tess).exists()), "path": tess or "",
        "version": _version([tess, "--version"]) if tess else "",
        "enables": "OCR text layers for scanned PDFs",
        "install": "winget install UB-Mannheim.TesseractOCR" if sys.platform == "win32"
                   else "brew install tesseract  (or apt install tesseract-ocr)",
    }

    from . import verapdf_setup
    vera = (getattr(cfg, "verapdf_path", "") or os.environ.get("VERAPDF_BAT")
            or shutil.which("verapdf") or shutil.which("verapdf.bat")
            or verapdf_setup.find())
    out["verapdf"] = {
        "label": "veraPDF",
        "ok": bool(vera and Path(vera).exists()), "path": vera or "",
        "version": "",
        "enables": "Prove PDF/UA-1 compliance (the census by rule)",
        "install": "The Studio fetches and installs it for you: "
                   "python -m courseforge tools --install --yes",
    }

    java = (getattr(cfg, "java_path", "") or os.environ.get("JAVACMD")
            or shutil.which("java") or _first_match(java_dirs()))
    out["java"] = {
        "label": "Java",
        "ok": bool(java and Path(java).exists()), "path": java or "",
        "version": _version([java, "-version"]) if java else "",
        "enables": "Runs veraPDF",
        "install": "winget install EclipseAdoptium.Temurin.21.JRE" if sys.platform == "win32"
                   else "brew install --cask temurin",
    }

    # --- python libraries ---------------------------------------------------------
    out["pymupdf"] = _module("pymupdf", "pymupdf", "PDF fixing, figure pictures, PDF text edits")
    out["pikepdf"] = _module("pikepdf", "pikepdf", "PDF tag trees and metadata")
    out["fonttools"] = _module("fontTools", "fonttools", "Embedding missing fonts in PDFs")
    out["pypdf"] = _module("pypdf", "pypdf", "PDF triage and the verify gate")
    out["python_pptx"] = _module("pptx", "python-pptx", "PowerPoint remediation")
    out["python_docx"] = _module("docx", "python-docx", "Word remediation")
    out["pillow"] = _module("PIL", "pillow", "Thumbnails and contact sheets")
    out["anthropic"] = _module("anthropic", "anthropic", "The API-key backend (hosted build only)")

    # --- claude cli -----------------------------------------------------------------
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    out["claude"] = {
        "label": "the Claude CLI",
        "ok": bool(claude and Path(claude).exists()), "path": claude if claude and Path(claude).exists() else "",
        "version": _version([claude, "--version"]) if claude and Path(claude).exists() else "",
        "enables": "Every model call in the local build",
        "install": "https://claude.com/claude-code",
    }

    out["pdf_engine"] = {
        "label": "the PDF engine",
        "ok": out["pymupdf"]["ok"] and out["pikepdf"]["ok"],
        "enables": "The whole PDF fixer",
        "install": "python -m pip install pymupdf pikepdf fonttools",
    }

    # Whether the Studio can get this one itself, which is what decides if the
    # "Install X to enable" card carries a button or only a command to copy.
    # The Claude CLI is deliberately not on the list: it is a login as much as
    # an install, and doing that behind someone's back would be wrong.
    winget_ok = sys.platform == "win32" and bool(shutil.which("winget"))
    for name, info in out.items():
        info["can_install"] = (
            winget_ok if name in ("tesseract", "java")
            else True if name in ("verapdf", "pymupdf", "pikepdf", "fonttools",
                                  "pypdf", "python_pptx", "python_docx",
                                  "pillow", "pdf_engine")
            else False)
    return out


def wire_env(cfg=None) -> None:
    """Point the PDF engine's own finders at what detect() found."""
    info = detect(cfg)
    if info["tesseract"]["ok"] and not os.environ.get("TESSERACT_EXE"):
        os.environ["TESSERACT_EXE"] = info["tesseract"]["path"]
    if info["verapdf"]["ok"] and not os.environ.get("VERAPDF_BAT"):
        os.environ["VERAPDF_BAT"] = info["verapdf"]["path"]
    if info["java"]["ok"] and not os.environ.get("JAVACMD"):
        os.environ["JAVACMD"] = info["java"]["path"]


# ---------------------------------------------------------------- installing
# What each missing tool needs, and whether a package manager can fetch it.
# winget is on every Windows 11 and checks the publisher's signature, so it is
# a better answer than this tool downloading executables on its own.
WINGET = {
    "tesseract": ("UB-Mannheim.TesseractOCR", "Tesseract OCR"),
    "java": ("EclipseAdoptium.Temurin.21.JRE", "Eclipse Temurin JRE 21"),
}
# Why a tool is not automated, shown next to it rather than buried in a log.
# Empty now that veraPDF installs itself; kept because the moment a tool cannot
# be automated, saying so beside its name is the right place for it.
MANUAL_WHY: dict[str, str] = {}
VERAPDF_STEPS = (
    "veraPDF has no winget package, so the Studio fetches the project's own\n"
    "installer, checks it against a known hash and runs it unattended into\n"
    "%LOCALAPPDATA%\\Programs\\veraPDF. It needs Java, which installs alongside.\n"
    "To do it by hand instead: https://verapdf.org/software/ , then set\n"
    "VERAPDF_BAT, or verapdf_path in config.json, if you put it elsewhere."
)


def install_plan(cfg=None) -> dict:
    """What is missing, and how each piece can be got. Nothing is run."""
    found = detect(cfg)
    winget_ok = bool(shutil.which("winget"))
    rows = []
    for name, (pkg, label) in WINGET.items():
        if not found.get(name, {}).get("ok"):
            rows.append({"tool": name, "label": label, "how": "winget",
                         "package": pkg, "command": f"winget install --id {pkg} --exact"})
    if not found.get("verapdf", {}).get("ok"):
        # Last, and deliberately so: it runs on Java, and the Java row above
        # may be what is about to provide it.
        from . import verapdf_setup
        rows.append({"tool": "verapdf", "label": "veraPDF", "how": "download",
                     "package": verapdf_setup.RELEASE, "command": "",
                     "steps": VERAPDF_STEPS,
                     "into": str(verapdf_setup.default_dir())})
    missing_py = [k for k in ("pymupdf", "pikepdf", "fonttools", "python_pptx", "python_docx")
                  if not found.get(k, {}).get("ok")]
    if missing_py:
        names = {"python_pptx": "python-pptx", "python_docx": "python-docx"}
        pkgs = " ".join(names.get(k, k) for k in missing_py)
        rows.append({"tool": "python", "label": "Python libraries", "how": "pip",
                     "package": pkgs, "command": f"{sys.executable} -m pip install {pkgs}"})
    return {"winget": winget_ok, "rows": rows, "found": found}


def install(cfg=None, yes: bool = False, say=print) -> int:
    """Install what a package manager can, and print the rest.

    Only ever runs a package manager the machine already trusts. This never
    downloads an executable itself: a tool that fetches and runs binaries is a
    worse thing to have on a machine than the inconvenience it saves.
    """
    plan = install_plan(cfg)
    if not plan["rows"]:
        say("Every optional tool is already here. Nothing to install.")
        return 0
    done = failed = 0
    for row in plan["rows"]:
        say("")
        say(f"{row['label']}: {plan['found'].get(row['tool'], {}).get('enables', '')}")
        if row["how"] == "manual":
            say(row["steps"])
            continue
        if row["how"] == "download":
            say(f"  the Studio fetches veraPDF {row['package']} and installs it "
                f"to {row['into']}")
            if not yes:
                say("  (re-run this with --yes to do it now)")
                continue
            from . import verapdf_setup
            out = verapdf_setup.install(cfg, on_line=lambda t: say("  " + t))
            if out["ok"]:
                done += 1
            else:
                failed += 1
                say(f"  that did not work: {out['detail']}")
            continue
        if row["how"] == "winget" and not plan["winget"]:
            say("  winget is not on this machine. Install it from the Microsoft Store "
                "(App Installer), or fetch the tool by hand.")
            failed += 1
            continue
        say(f"  {row['command']}")
        if not yes:
            say("  (run the line above, or re-run this with --yes to do it now)")
            continue
        cmd = ([shutil.which("winget"), "install", "--id", row["package"], "--exact",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity"] if row["how"] == "winget"
               else [sys.executable, "-m", "pip", "install"] + row["package"].split())
        say("  installing...")
        result = subprocess.run(cmd, creationflags=NO_WINDOW)
        if result.returncode == 0:
            done += 1
            say("  done")
        else:
            failed += 1
            say(f"  that did not work (exit {result.returncode}). Run the line above by hand.")
    if done:
        say("")
        say("Installed something. Open a new terminal so it is on your PATH, "
            "then run: python -m courseforge doctor")
    return 1 if failed else 0


# --------------------------------------------------------------- first run
# Asked once, on the first launch, and remembered. The answer lives beside the
# token rather than in the app folder, so re-downloading the app does not ask
# again and a second copy on the same machine inherits the decision.
SETUP_FILE = "tools-setup.json"


def _setup_path() -> Path:
    from .config import user_dir
    return user_dir() / SETUP_FILE


def setup_state() -> dict:
    try:
        return json.loads(_setup_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_setup(**changes) -> dict:
    state = setup_state()
    state.update(changes)
    state["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = _setup_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return state


def forget_setup() -> None:
    """Throw away the remembered answer so the offer comes back."""
    try:
        _setup_path().unlink()
    except OSError:
        pass


def should_offer_setup(cfg=None) -> dict | None:
    """The first-run offer, or None when there is nothing to say.

    Nothing to say means: already asked, or every optional tool is present.
    A missing tool is never a reason to stop someone using the app, so this is
    an offer and the caller must let them past it either way.
    """
    state = setup_state()
    if state.get("asked") or state.get("never"):
        return None
    plan = install_plan(cfg)
    if not plan["rows"]:
        remember_setup(asked=True, installed=[], note="nothing was missing")
        return None
    return plan


def install_stream(cfg=None, on_line=None, only=None) -> dict:
    """install(), but reporting line by line so a window can show progress.

    `only` limits it to named tools. Returns what happened per tool; a failure
    is reported, never raised, because the app runs without any of this.
    """
    say = on_line or (lambda _t: None)
    plan = install_plan(cfg)
    rows = [r for r in plan["rows"] if not only or r["tool"] in only]
    done, failed, manual = [], [], []
    for row in rows:
        if row["how"] == "manual":
            manual.append(row["tool"])
            say(f"{row['label']}: has to be done by hand, see the notes afterwards.")
            continue
        if row["how"] == "download":
            from . import verapdf_setup
            out = verapdf_setup.install(cfg, on_line=say)
            if out["ok"]:
                done.append(row["tool"])
            else:
                failed.append(row["tool"])
                say(f"{row['label']}: {out['detail']}")
            continue
        if row["how"] == "winget" and not plan["winget"]:
            failed.append(row["tool"])
            say(f"{row['label']}: winget is not on this machine.")
            continue
        say(f"Installing {row['label']}...")
        cmd = ([shutil.which("winget"), "install", "--id", row["package"], "--exact",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity"] if row["how"] == "winget"
               else [sys.executable, "-m", "pip", "install"] + row["package"].split())
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace",
                                    creationflags=NO_WINDOW)
            for line in proc.stdout or ():
                line = line.rstrip()
                if line:
                    say("   " + line[:120])
            code = proc.wait()
        except Exception as exc:  # noqa: BLE001
            code, _ = 1, say(f"   {type(exc).__name__}: {exc}")
        if code == 0:
            done.append(row["tool"])
            say(f"{row['label']}: installed.")
        else:
            failed.append(row["tool"])
            say(f"{row['label']}: did not install (exit {code}). "
                "You can do it later from a terminal.")
    return {"installed": done, "failed": failed, "manual": manual,
            "steps": VERAPDF_STEPS if manual else ""}
