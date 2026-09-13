"""Install veraPDF without anyone clicking through a wizard.

veraPDF is the only tool the Studio needs that no package manager carries. It
ships as an IzPack installer -- a signed jar that normally opens a five-screen
wizard -- and for a long time this was left as a manual step on the grounds
that a tool which downloads and runs binaries is a worse thing to own than the
inconvenience it saves.

That reasoning still holds for arbitrary downloads. It does not hold here: the
release is pinned, the URL is the project's own, and the archive is checked
against a hash written down in this file before a single byte of it is run. If
the hash does not match, nothing runs and the download is deleted.

IzPack has supported unattended installation since forever. It reads a script
naming each panel of the wizard and the answer to give it, so the five screens
become five elements. The panel class names and ids are not guessable -- they
are whatever the installer was built with -- so they are read out of the jar's
own `resources/panelsOrder` and written down in PANELS below.

The trap, and the reason two earlier attempts looked like the format was
wrong: IzPack takes a single-instance lock in the temp folder, and an
installer that was killed rather than closed leaves the lock behind. Every
later run then refuses, with a message about another copy already running, and
prints `[ Automated installation FAILED! ]` -- which reads exactly like a
rejected script. It is not. `clear_stale_lock` removes it, which is what the
installer's own message tells a person to do.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

RELEASE = "1.30.2"
URL = ("https://software.verapdf.org/rel/1.30/"
       f"verapdf-greenfield-{RELEASE}-installer.zip")
# sha256 of the zip above. The install refuses to run anything that does not
# match this, so a hijacked mirror or a truncated download is a refusal rather
# than a surprise.
SHA256 = "6cc6341cb1af644044054b81f00a6590a7918abb18f762243de115258bcad838"
SIZE = 32_923_960
JAR = f"verapdf-izpack-installer-{RELEASE}.jar"

# The wizard, as the installer itself records it. Read from the jar with:
#   python -c "import zipfile,re; print(re.findall(rb'[ -~]{4,}',
#     zipfile.ZipFile(JAR).read('resources/panelsOrder')))"
# Changing the pinned RELEASE means reading it again: a panel list that does
# not match the installer is the one thing that really does make it fail.
PANELS = [
    ("com.izforge.izpack.panels.htmlhello.HTMLHelloPanel", "welcome"),
    ("com.izforge.izpack.panels.target.TargetPanel", "install_dir"),
    ("com.izforge.izpack.panels.packs.PacksPanel", "sdk_pack_select"),
    ("com.izforge.izpack.panels.install.InstallPanel", "install"),
    ("com.izforge.izpack.panels.finish.FinishPanel", "finish"),
]
# The four packs it offers, in order. The Studio shells out to the command line
# validator and never opens the desktop app, but the GUI pack carries shared
# jars the CLI wrapper loads, so both go in. The sample plugins and the manual
# are 20 MB of things nothing here reads.
PACKS = [("veraPDF GUI", True), ("veraPDF CLI", True),
         ("veraPDF Documentation", False), ("veraPDF Sample Plugins", False)]

LOCK = "iz-veraPDF Software.tmp"


def default_dir() -> Path:
    """Somewhere an ordinary account can actually write.

    Not Program Files: installing there needs administrator rights, and asking
    an instructor on a managed laptop for those is how this ends up not
    happening at all. Per-user is the right scope anyway -- the Studio is the
    only thing that runs it.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / "veraPDF"
    if sys.platform == "darwin":
        return Path.home() / "Applications" / "veraPDF"
    return Path.home() / ".local" / "opt" / "verapdf"


def launcher(root: Path) -> Path:
    """The command-line validator inside an installed copy."""
    return Path(root) / ("verapdf.bat" if sys.platform == "win32" else "verapdf")


def search_dirs() -> list[Path]:
    """Every place an installed veraPDF might be, newest answer first."""
    return [default_dir(),
            Path(r"C:\Program Files\veraPDF"),
            Path(r"C:\Program Files (x86)\veraPDF"),
            Path.home() / "verapdf",
            Path.home() / "tools" / "verapdf",
            Path("/opt/verapdf"),
            Path("/usr/local/verapdf")]


def find() -> str:
    for root in search_dirs():
        try:
            path = launcher(root)
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return ""


def auto_xml(install_dir: Path) -> str:
    """The unattended installation script: one element per wizard screen."""
    rows = []
    for klass, panel_id in PANELS:
        if klass.endswith("TargetPanel"):
            rows.append(f'  <{klass} id="{panel_id}">\n'
                        f'    <installpath>{install_dir}</installpath>\n'
                        f'  </{klass}>')
        elif klass.endswith("PacksPanel"):
            packs = "\n".join(
                f'    <pack index="{i}" name="{name}" '
                f'selected="{str(on).lower()}"/>'
                for i, (name, on) in enumerate(PACKS))
            rows.append(f'  <{klass} id="{panel_id}">\n{packs}\n  </{klass}>')
        else:
            rows.append(f'  <{klass} id="{panel_id}"/>')
    body = "\n".join(rows)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
            f'<AutomatedInstallation langpack="eng">\n{body}\n'
            '</AutomatedInstallation>\n')


def clear_stale_lock(say=lambda _t: None) -> bool:
    """Remove the single-instance lock an interrupted installer left behind.

    Only ever the veraPDF one, only in the temp folder, and only when it is
    empty -- IzPack writes the running process's port into it, so a lock with
    content might belong to an installer that really is open.
    """
    path = Path(tempfile.gettempdir()) / LOCK
    try:
        if not path.is_file():
            return False
        if path.stat().st_size:
            say("An installer lock file has something in it, so it is being "
                "left alone. Close any open veraPDF installer and try again.")
            return False
        path.unlink()
        say("Cleared a lock file left by an interrupted installer.")
        return True
    except OSError:
        return False


def _download(dest: Path, say) -> Path:
    import urllib.request
    say(f"Downloading veraPDF {RELEASE} ({SIZE // 1_000_000} MB) from verapdf.org...")
    zip_path = dest / "verapdf-installer.zip"
    got = 0
    step = 8 * 1024 * 1024
    next_note = step
    digest = hashlib.sha256()
    req = urllib.request.Request(URL, headers={"User-Agent": "CourseForge-Studio"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as fh:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            digest.update(chunk)
            got += len(chunk)
            if got >= next_note:
                say(f"   {got // 1_000_000} MB")
                next_note += step
    if digest.hexdigest() != SHA256:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(
            "the download did not match the hash this version was built "
            "against, so nothing was run and it has been deleted. Either the "
            "file was corrupted on the way, or what is being served is not the "
            f"veraPDF {RELEASE} release. Install it by hand from "
            "https://verapdf.org/software/ instead.")
    say(f"   {got // 1_000_000} MB, hash matches.")
    return zip_path


def _unpack(zip_path: Path, dest: Path, say) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.endswith(JAR)]
        if not members:
            raise RuntimeError(f"the archive does not contain {JAR}.")
        name = members[0]
        # Archives are untrusted input even when the hash matched: take the one
        # entry wanted, by its own basename, rather than extracting the tree.
        target = dest / JAR
        with zf.open(name) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
    say("Unpacked the installer.")
    return target


def install(cfg=None, on_line=None, dest: Path | str | None = None) -> dict:
    """Fetch, check and run the installer unattended. Never raises.

    Returns {"ok", "path", "detail", "dir"}. A failure is a sentence, because
    every caller of this shows it to a person and carries on: the Studio works
    without veraPDF, minus the PDF/UA proof.
    """
    say = on_line or (lambda _t: None)
    from . import tools
    found = tools.detect(cfg)

    java = found.get("java", {}).get("path") or ""
    if not (java and Path(java).exists()):
        return {"ok": False, "path": "", "dir": "",
                "detail": "veraPDF runs on Java, and there is no Java here yet. "
                          "Install that first -- the same button does it."}

    root = Path(dest) if dest else default_dir()
    work = Path(tempfile.mkdtemp(prefix="cf-verapdf-"))
    try:
        zip_path = _download(work, say)
        jar = _unpack(zip_path, work, say)
        script = work / "auto-install.xml"
        script.write_text(auto_xml(root), encoding="utf-8")
        clear_stale_lock(say)

        say(f"Installing to {root} ...")
        env = dict(os.environ, JAVACMD=java)
        proc = subprocess.Popen([java, "-jar", str(jar), str(script)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, creationflags=NO_WINDOW)
        tail: list[str] = []
        for line in proc.stdout or ():
            line = line.rstrip()
            if line:
                tail.append(line)
                say("   " + line[:120])
        code = proc.wait()

        path = launcher(root)
        if code != 0 or not path.is_file():
            hint = ""
            if any("already running" in t for t in tail):
                hint = (" An installer lock file is in the way: close any open "
                        "veraPDF installer, then try again.")
            return {"ok": False, "path": "", "dir": str(root),
                    "detail": f"the installer exited {code} and left nothing at "
                              f"{root}.{hint}"}

        # Prove it runs before calling it installed. The launcher shells out to
        # `java` unless JAVACMD is set, and a Java that is installed but not on
        # PATH is the normal case, so this would otherwise pass here and fail
        # the first time the PDF area asked it a question.
        version = ""
        try:
            out = subprocess.run([str(path), "--version"], capture_output=True,
                                 text=True, timeout=90, env=env,
                                 creationflags=NO_WINDOW)
            version = (out.stdout or "").strip().splitlines()[:1]
            version = version[0] if version else ""
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": str(path), "dir": str(root),
                    "detail": f"it installed, but would not run: {type(exc).__name__}: {exc}"}
        if not version:
            return {"ok": False, "path": str(path), "dir": str(root),
                    "detail": "it installed, but did not answer --version. It may "
                              "need a Java on the PATH."}
        os.environ["VERAPDF_BAT"] = str(path)
        say(f"{version} is installed and answering.")
        return {"ok": True, "path": str(path), "dir": str(root), "detail": version}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "path": "", "dir": str(root),
                "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
