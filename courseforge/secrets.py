"""Where the Canvas token lives, and how it is protected at rest.

A Canvas API token is a bearer credential for a whole account. A plaintext copy
sitting in a working folder gets emailed, zipped, copied to a shared drive and
synced to OneDrive without anyone deciding to do that. So:

- On Windows the token is stored DPAPI-encrypted at user scope (`canvas.token.enc`),
  readable only by the Windows account that saved it, on the machine that saved
  it. The blob is hex, and the plaintext inside is UTF-16LE, which is exactly
  what PowerShell's `ConvertFrom-SecureString` (no -Key) produces. So a file this
  module writes is readable by the legacy CourseForge PowerShell scripts, and
  vice versa.
- Elsewhere it is a plain file with 0600 permissions. There is no DPAPI on
  macOS/Linux; the OS user boundary is the protection.

Nothing here reads config.json, and no function prints a token.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
ENC_NAME = "canvas.token.enc"
PLAIN_NAME = "canvas.token"


class TokenError(RuntimeError):
    pass


# ------------------------------------------------------------------- DPAPI
if IS_WINDOWS:
    import ctypes.wintypes as _wt

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", _wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _dpapi(data: bytes, protect: bool) -> bytes:
        inp = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                           ctypes.POINTER(ctypes.c_char)))
        out = _BLOB()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        # CRYPTPROTECT_UI_FORBIDDEN = 1: never pop a dialog from a background job.
        ok = fn(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out))
        if not ok:
            raise TokenError("Windows refused to %s the token (DPAPI error %d)."
                             % ("protect" if protect else "unprotect",
                                ctypes.GetLastError()))
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)
else:
    def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover
        raise TokenError("DPAPI is only available on Windows.")


def protect_text(token: str) -> str:
    """Token -> hex DPAPI blob in the PowerShell-compatible (UTF-16LE) format."""
    return _dpapi(token.encode("utf-16-le"), True).hex()


def unprotect_text(blob_hex: str) -> str:
    raw = _dpapi(bytes.fromhex(blob_hex.strip()), False)
    return raw.decode("utf-16-le").strip("\x00")


def unprotect_bin(blob: bytes) -> str:
    """The other legacy format: a raw DPAPI blob whose plaintext is UTF-8
    (`%LOCALAPPDATA%\\CourseForge-PDF\\token-<host>.bin` and the Assistant's copy)."""
    return _dpapi(blob, False).decode("utf-8").strip()


# ------------------------------------------------------------ file helpers
def clean(raw: str) -> str:
    """A token as the user meant it, whatever their editor or shell added."""
    value = raw.lstrip("﻿").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def plausible(token: str) -> bool:
    return 20 <= len(token) <= 300 and not any(c.isspace() for c in token) and "://" not in token


def read_token_file(path: Path) -> str:
    """Read a token from any of the formats this project has ever written."""
    name = path.name.lower()
    if name.endswith(".enc"):
        return clean(unprotect_text(path.read_text(encoding="ascii")))
    if name.endswith(".bin"):
        return clean(unprotect_bin(path.read_bytes()))
    return clean(path.read_text(encoding="utf-8-sig"))


def write_token(dir_path: Path, token: str) -> Path:
    """Save the token under `dir_path`, encrypted where the platform allows.

    Returns the path written. Any plaintext copy beside it is removed once the
    encrypted copy exists, so a migration never leaves two truths on disk.
    """
    token = clean(token)
    if not plausible(token):
        raise TokenError("That does not look like a Canvas token.")
    dir_path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        path = dir_path / ENC_NAME
        tmp = path.with_suffix(".enc.part")
        tmp.write_text(protect_text(token), encoding="ascii", newline="")
        os.replace(tmp, path)
        plain = dir_path / PLAIN_NAME
        if plain.exists():
            try:
                plain.unlink()
            except OSError:
                pass
        return path
    path = dir_path / PLAIN_NAME
    tmp = path.with_suffix(".token.part")
    tmp.write_text(token, encoding="utf-8", newline="")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def legacy_token_files(host: str = "") -> list[Path]:
    """Every place an earlier CourseForge or canvas-grader copy may have left a
    token, newest convention first. Read-only: nothing here is written to."""
    out: list[Path] = []
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    home = Path.home()
    if appdata:
        out.append(Path(appdata) / "canvas-grader" / PLAIN_NAME)
    if local and host:
        out.append(Path(local) / "CourseForge-Assistant" / f"token-{host}.bin")
        out.append(Path(local) / "CourseForge-PDF" / f"token-{host}.bin")
    for docs in _documents_dirs():
        out.append(docs / "canvas-work" / ENC_NAME)
        out.append(docs / "canvas-work" / PLAIN_NAME)
    out.append(home / ".config" / "canvas-grader" / PLAIN_NAME)
    out.append(home / ".canvas.token")
    return out


def _documents_dirs() -> list[Path]:
    """Both Documents folders. OneDrive Known Folder Move makes
    %USERPROFILE%\\Documents and the shell's MyDocuments two different places."""
    seen: list[Path] = []
    home = Path.home()
    seen.append(home / "Documents")
    if IS_WINDOWS:
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                p = Path(buf.value)
                if p not in seen:
                    seen.append(p)
        except Exception:  # noqa: BLE001
            pass
    od = os.environ.get("OneDrive")
    if od:
        p = Path(od) / "Documents"
        if p not in seen:
            seen.append(p)
    return seen
