; CourseForge PDF Fixer - Inno Setup script
; Per-user install (no admin needed on school machines), Start Menu +
; optional Desktop shortcut, LZMA2 compression.

#define AppName "CourseForge PDF Fixer"
#define AppVersion "1.1.8"
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

[Files]
Source: "dist\courseforge-pdf\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion
; brand-versioned NAME: Windows caches shortcut icons by path, so a rebrand
; must ship under a fresh filename or old machines keep showing stale art
Source: "cf-icon.ico"; DestDir: "{app}"; DestName: "cf-icon-mgccc.ico"

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



