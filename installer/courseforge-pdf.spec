# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['pypdf']
hiddenimports += collect_submodules('fontTools')
tmp_ret = collect_all('pymupdf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pikepdf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


# Source resolution, in order: CF_SCRIPTS when set explicitly, then the
# checked-in copy in this repo (..\skill\scripts or ..\scripts), then the
# live skill in the user profile as a LAST resort. The repo copy comes first
# so what is built is what is committed - a developer's working skill folder
# with uncommitted edits used to win, and the installer shipped it silently.
import os
_here = os.path.dirname(os.path.abspath(SPEC))
_live = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "courseforge", "scripts")
_scripts = os.environ.get("CF_SCRIPTS") or ""
for _cand in ([_scripts] if _scripts else []) + [
        os.path.join(_here, "..", "skill", "scripts"),
        os.path.join(_here, "..", "scripts"),
        os.path.join(_here, "src"), _live]:
    if os.path.isfile(os.path.join(_cand, "courseforge_gui.py")):
        _scripts = os.path.abspath(_cand)
        break
else:
    raise SystemExit(
        "courseforge_gui.py not found. Point CF_SCRIPTS at the courseforge "
        r"skill's scripts folder, e.g. %USERPROFILE%\.claude\skills"
        r"\courseforge\scripts")

a = Analysis(
    [os.path.join(_scripts, 'courseforge_gui.py')],
    pathex=[_scripts],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='courseforge-pdf',
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
    icon=['cf-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='courseforge-pdf',
)
