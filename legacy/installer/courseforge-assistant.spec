# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CourseForge Assistant (windowed, one-folder).
#
# Deliberately small: the Assistant does not import the PDF engine, so no
# PyMuPDF / pikepdf / fontTools go into the exe. Those packages ship in the
# bundled embeddable Python (python\) that Build-Assistant.ps1 stages beside
# the exe, where the skill's own tools - and the permission hook - run.
#
# The build script stages, next to the exe:   python\   skill\   hooks\
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

import os
_here = os.path.dirname(os.path.abspath(SPEC))
# repo copy first, the live skill in the profile last (see courseforge-pdf.spec)
_live = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "courseforge", "scripts")
_scripts = os.environ.get("CF_SCRIPTS") or ""
for _cand in ([_scripts] if _scripts else []) + [
        os.path.join(_here, "..", "skill", "scripts"),
        os.path.join(_here, "src"), _live]:
    if os.path.isfile(os.path.join(_cand, "courseforge_assistant.py")):
        _scripts = os.path.abspath(_cand)
        break
else:
    raise SystemExit(
        "courseforge_assistant.py not found. Point CF_SCRIPTS at the courseforge "
        r"skill's scripts folder, e.g. %USERPROFILE%\.claude\skills\courseforge\scripts")

# the hook runs as a plain .py under the bundled Python, so ship the source
datas += [(os.path.join(_scripts, 'cf_assistant_hook.py'), 'hooks')]
datas += [(os.path.join(_here, 'cf-assistant-icon.ico'), '.')]

a = Analysis(
    [os.path.join(_scripts, 'courseforge_assistant.py')],
    pathex=[_scripts],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pymupdf', 'fitz', 'pikepdf', 'fontTools', 'pypdf',
              'numpy', 'docx', 'pptx', 'lxml'],   # PIL stays: customtkinter needs it
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='courseforge-assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(_here, 'cf-assistant-icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='courseforge-assistant',
)
