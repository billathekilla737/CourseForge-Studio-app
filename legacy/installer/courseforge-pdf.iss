; CourseForge PDF Fixer - Inno Setup script
; Per-user install (no admin needed on school machines), Start Menu +
; optional Desktop shortcut, LZMA2 compression.

#define AppName "CourseForge PDF Fixer"
#define AppVersion "1.1.9"
#define AppExe "courseforge-pdf.exe"

[Setup]
AppId={{8E3F2C1A-7D4B-4E9A-9C6A-CFPDF0000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=MGCCC CourseForge
DefaultDirName={userpf}\CourseForge-PDF-Fixer
DefaultGroupName=CourseForge
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputBaseFilename=CourseForge-PDF-Fixer-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
SetupIconFile=cf-icon.ico

; Code signing. Build-*.ps1 passes /DSign and defines the "cfsign" tool
; (/Scfsign="signtool.exe sign ... $f") when a certificate is available; the
; setup exe and the uninstaller are then signed too. Without /DSign the build
; is unsigned and SmartScreen will warn on a browser-downloaded copy.
#ifdef Sign
SignTool=cfsign
SignedUninstaller=yes
#endif

[Files]
Source: "dist\courseforge-pdf\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion
; brand-versioned NAME: Windows caches shortcut icons by path, so a rebrand
; must ship under a fresh filename or old machines keep showing stale art
Source: "cf-icon.ico"; DestDir: "{app}"; DestName: "cf-icon-mgccc.ico"

; An upgrade installed over the top used to KEEP every file the new build
; no longer ships (ignoreversion only overwrites what exists in both). Clear
; the frozen tree first so the app on disk is exactly the build.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

; The DPAPI token blobs live outside {app}; take them with the app so an
; uninstall on a PC being handed over does not leave a usable credential.
; Course folders under Documents (originals, fixed PDFs, reports) are kept.
[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\CourseForge-PDF"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\cf-icon-mgccc.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  IconFilename: "{app}\cf-icon-mgccc.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; \
  GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent



