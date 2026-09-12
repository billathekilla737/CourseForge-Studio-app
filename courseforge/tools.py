"""Which optional tools this machine has, and how to get the missing ones.

The UI shows an "install X to enable" card for anything missing and disables
the verbs that need it, rather than failing halfway through a job.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
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


def _module(name: str, pip: str, enables: str) -> dict:
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "") or getattr(mod, "VersionBind", "") or ""
        return {"ok": True, "version": str(ver), "enables": enables}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "version": "", "enables": enables,
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
        "ok": bool(tess and Path(tess).exists()), "path": tess or "",
        "version": _version([tess, "--version"]) if tess else "",
        "enables": "OCR text layers for scanned PDFs",
        "install": "winget install UB-Mannheim.TesseractOCR" if sys.platform == "win32"
                   else "brew install tesseract  (or apt install tesseract-ocr)",
    }

    vera = (getattr(cfg, "verapdf_path", "") or os.environ.get("VERAPDF_BAT")
            or shutil.which("verapdf") or shutil.which("verapdf.bat"))
    if not vera:
        for cand in (r"C:\Program Files\veraPDF\verapdf.bat",
                     Path.home() / "verapdf" / "verapdf.bat",
                     Path.home() / "tools" / "verapdf" / "verapdf.bat"):
            if Path(cand).is_file():
                vera = str(cand)
                break
    out["verapdf"] = {
        "ok": bool(vera and Path(vera).exists()), "path": vera or "",
        "version": "",
        "enables": "Prove PDF/UA-1 compliance (the census by rule)",
        "install": "Download the veraPDF greenfield installer from https://verapdf.org/ "
                   "and install to C:\\Program Files\\veraPDF (needs Java).",
    }

    java = getattr(cfg, "java_path", "") or os.environ.get("JAVACMD") or shutil.which("java")
    if not java:
        for cand in sorted((Path.home() / "tools").glob("jdk-*/bin/java.exe"), reverse=True):
            java = str(cand)
            break
    out["java"] = {
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
        "ok": bool(claude and Path(claude).exists()), "path": claude if claude and Path(claude).exists() else "",
        "version": _version([claude, "--version"]) if claude and Path(claude).exists() else "",
        "enables": "Every model call in the local build",
        "install": "https://claude.com/claude-code",
    }

    out["pdf_engine"] = {
        "ok": out["pymupdf"]["ok"] and out["pikepdf"]["ok"],
        "enables": "The whole PDF fixer",
        "install": "python -m pip install pymupdf pikepdf fonttools",
    }
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
