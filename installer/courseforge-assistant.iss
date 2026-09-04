; CourseForge Assistant - Inno Setup script
; Per-user install (no admin needed on school machines), Start Menu +
; optional Desktop shortcut, LZMA2 compression. Same shape as the PDF Fixer's
; installer so the two apps install and uninstall the same way.
;
; Build-Assistant.ps1 stages dist-assistant\courseforge-assistant\ with the
; exe, _internal\, python\ (embeddable CPython + the skill's packages),
; skill\ (the courseforge skill) and hooks\ before this is compiled.

#define AppName "CourseForge Assistant"
#define AppVersion "0.1.0"
#define AppExe "courseforge-assistant.exe"

[Setup]
AppId={{5B7D9E42-3C1A-4F8B-9D2E-1A2B3C4D5E61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=MGCCC CourseForge
DefaultDirName={userpf}\CourseForge-Assistant
DefaultGroupName=CourseForge
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputBaseFilename=CourseForge-Assistant-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
SetupIconFile=cf-assistant-icon.ico

[Files]
Source: "dist-assistant\courseforge-assistant\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion
; brand-versioned NAME: Windows caches shortcut icons by path, so a rebrand
; must ship under a fresh filename or old machines keep showing stale art
Source: "cf-assistant-icon.ico"; DestDir: "{app}"; DestName: "cf-assistant-mgccc.ico"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\cf-assistant-mgccc.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  IconFilename: "{app}\cf-assistant-mgccc.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; \
  GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent
