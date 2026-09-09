<#
  Build-PdfFixer.ps1 - build CourseForge PDF Fixer end to end.

    .\Build-PdfFixer.ps1                        # tests -> selftest -> PyInstaller -> tools -> verify -> smoke -> installer
    .\Build-PdfFixer.ps1 -ToolsFrom ..\dist-1107\courseforge-pdf   # copy tesseract\ verapdf\ jre\ from an earlier build
    .\Build-PdfFixer.ps1 -SkipInstaller         # stop before Inno Setup
    .\Build-PdfFixer.ps1 -SignThumbprint <sha1> # sign the exe, the setup exe and its uninstaller
    .\Build-PdfFixer.ps1 -AllowDirty            # local test build from uncommitted source
    .\Build-PdfFixer.ps1 -RecordToolHashes      # after a DELIBERATE tool upgrade: rewrite bundled-tools.json

  The third-party tools (Tesseract, veraPDF, a Temurin JRE) are not built here.
  A maintainer downloads them from their publishers once and places the folders
  in dist\courseforge-pdf\ (or points -ToolsFrom at a previous build). Before
  anything is packaged, every file listed in bundled-tools.json must match its
  SHA-256, so a swapped or tampered binary cannot ride into the installer.

  Source is the checked-in skill\scripts (or ..\scripts in the standalone
  repo); the build refuses uncommitted changes unless -AllowDirty.
  ASCII only. PowerShell 5.1 compatible.
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [switch]$Clean,
    [switch]$AllowDirty,
    [switch]$RecordToolHashes,
    [string]$ToolsFrom = '',
    [string]$SignThumbprint = '',
    [string]$SignCommand = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
Set-Location $here
. (Join-Path $here 'Sign-Common.ps1')

function Step($m) { Write-Host ""; Write-Host ("== {0}" -f $m) -ForegroundColor Cyan }
function Ok($m)   { Write-Host ("   OK  {0}" -f $m) -ForegroundColor Green }
function Die($m)  { Write-Host ("   X   {0}" -f $m) -ForegroundColor Red; exit 1 }

# ---- 0. source
$repoRoot = (Resolve-Path (Join-Path $here '..')).Path
$scripts = $env:CF_SCRIPTS
if (-not $scripts) {
    foreach ($cand in @((Join-Path $repoRoot 'skill\scripts'), (Join-Path $repoRoot 'scripts'))) {
        if (Test-Path (Join-Path $cand 'courseforge_gui.py')) { $scripts = $cand; break }
    }
}
if (-not $scripts -or -not (Test-Path (Join-Path $scripts 'courseforge_gui.py'))) { Die "courseforge_gui.py not found (set CF_SCRIPTS)." }
$scripts = (Resolve-Path $scripts).Path
Ok ("source: {0}" -f $scripts)
if ($scripts.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    $rel = $scripts.Substring($repoRoot.Length).TrimStart('\') -replace '\\scripts$', ''
    $head = Assert-CleanSource -RepoRoot $repoRoot -Paths @($rel, 'installer') -AllowDirty:$AllowDirty
    if ($head) { Ok ("building commit {0}" -f $head) }
} elseif (-not $AllowDirty) {
    Die "CF_SCRIPTS points outside this repo; pass -AllowDirty for a local test build."
}
$signTemplate = Get-SignCommandTemplate -Thumbprint $SignThumbprint -Command $SignCommand -TimestampUrl $TimestampUrl
if ($signTemplate) { Ok "code signing: on" } else { Write-Host "   unsigned build (no -SignThumbprint / -SignCommand)" -ForegroundColor Yellow }

$dist  = Join-Path $here 'dist'
$work  = Join-Path $here 'build'
$stage = Join-Path $dist 'courseforge-pdf'
$toolsJson = Join-Path $here 'bundled-tools.json'

if ($Clean) {
    Step "Clean"
    foreach ($d in @($stage, $work)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d; Ok ("removed {0}" -f $d) } }
}

# ---- 1. tests + engine selftest
if (-not $SkipTests) {
    Step "Tests"
    $t = Join-Path $scripts 'test_courseforge_pdf.py'
    if (Test-Path $t) { & python $t; if ($LASTEXITCODE -ne 0) { Die "test_courseforge_pdf.py failed" } }
    & python (Join-Path $scripts 'pdf_fastlane.py') selftest
    if ($LASTEXITCODE -ne 0) { Die "pdf_fastlane.py selftest failed" }
    Ok "tests and selftest pass"
}

# ---- 2. freeze (keep the tool folders if they are already staged)
Step "PyInstaller"
$keep = Join-Path $here '_tools-keep'
if (Test-Path $keep) { Remove-Item -Recurse -Force $keep }
foreach ($d in @('tesseract', 'verapdf', 'jre')) {
    $p = Join-Path $stage $d
    if (Test-Path $p) { New-Item -ItemType Directory -Force $keep | Out-Null; Move-Item $p (Join-Path $keep $d) }
}
$env:CF_SCRIPTS = $scripts
& python -m PyInstaller (Join-Path $here 'courseforge-pdf.spec') --distpath $dist --workpath $work --noconfirm
if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
$exe = Join-Path $stage 'courseforge-pdf.exe'
if (-not (Test-Path $exe)) { Die "no exe produced" }
if (Test-Path $keep) { Get-ChildItem $keep -Directory | ForEach-Object { Move-Item $_.FullName (Join-Path $stage $_.Name) }; Remove-Item -Recurse -Force $keep }
Ok "frozen"

# ---- 3. third-party tools beside the exe
Step "Bundled tools"
if ($ToolsFrom) {
    foreach ($d in @('tesseract', 'verapdf', 'jre')) {
        $src = Join-Path $ToolsFrom $d
        if (-not (Test-Path $src)) { Die "$d\ not found under -ToolsFrom $ToolsFrom" }
        $dst = Join-Path $stage $d
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        Copy-Item -Recurse $src $dst
    }
    Ok ("copied tesseract\, verapdf\, jre\ from {0}" -f $ToolsFrom)
}
foreach ($d in @('tesseract', 'verapdf', 'jre')) {
    if (-not (Test-Path (Join-Path $stage $d))) { Die "$d\ is missing from $stage - download it from the publisher (see bundled-tools.json) or pass -ToolsFrom" }
}
$manifest = Get-Content -Raw $toolsJson | ConvertFrom-Json
if ($RecordToolHashes) {
    foreach ($tool in $manifest.tools) {
        foreach ($f in @($tool.files.PSObject.Properties.Name)) {
            $tool.files.$f = (Get-FileHash -Algorithm SHA256 (Join-Path $stage $f)).Hash.ToLower()
        }
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $toolsJson -Encoding UTF8
    Write-Host "   bundled-tools.json rewritten from the staged files - verify them against the publishers' downloads and commit" -ForegroundColor Yellow
}
$bad = 0
foreach ($tool in $manifest.tools) {
    foreach ($f in @($tool.files.PSObject.Properties.Name)) {
        $p = Join-Path $stage $f
        if (-not (Test-Path $p)) { Write-Host ("   X   missing: {0}" -f $f) -ForegroundColor Red; $bad++; continue }
        $got = (Get-FileHash -Algorithm SHA256 $p).Hash.ToLower()
        if ($got -ne $tool.files.$f.ToLower()) { Write-Host ("   X   {0} does not match bundled-tools.json" -f $f) -ForegroundColor Red; $bad++ }
    }
}
if ($bad) { Die "$bad bundled tool file(s) failed verification. If this is a deliberate upgrade, re-run with -RecordToolHashes and verify the new hashes against the publisher." }
Ok ("{0} tool files match bundled-tools.json" -f (($manifest.tools | ForEach-Object { @($_.files.PSObject.Properties).Count } | Measure-Object -Sum).Sum))

# ---- 4. smoke
Step "Smoke test"
$p = Start-Process -FilePath $exe -ArgumentList '--smoke' -PassThru -Wait
if ($p.ExitCode -ne 0) { Die ("--smoke exited {0} (see Documents\CourseForge-PDF\startup-error.txt)" -f $p.ExitCode) }
Ok "window opens and closes"
# the exe is a windowed app, so "& $exe" would return at once: wait on it
$p = Start-Process -FilePath $exe -ArgumentList 'selftest' -PassThru -Wait
if ($p.ExitCode -ne 0) { Die ("frozen exe selftest exited {0}" -f $p.ExitCode) }
Ok "frozen exe passes the engine selftest with the bundled tools"

# ---- 5. sign
if ($signTemplate) {
    Step "Sign"
    Invoke-Sign -Template $signTemplate -File $exe | Out-Null
    Ok "courseforge-pdf.exe signed"
}

# ---- 6. installer
if ($SkipInstaller) { Write-Host ""; Write-Host "Done (installer skipped)." -ForegroundColor Green; exit 0 }
Step "Inno Setup"
$iscc = $null
foreach ($cand in @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
                    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                    'C:\Program Files\Inno Setup 6\ISCC.exe')) {
    if (Test-Path $cand) { $iscc = $cand; break }
}
if (-not $iscc) { $c = Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($c) { $iscc = $c.Source } }
if (-not $iscc) { Die "ISCC.exe (Inno Setup 6) not found - install it or re-run with -SkipInstaller" }
$isccArgs = @((Join-Path $here 'courseforge-pdf.iss')) + (ConvertTo-InnoSignSwitch -Template $signTemplate)
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { Die "Inno Setup failed" }
$setup = Get-ChildItem (Join-Path $here 'Output') -Filter 'CourseForge-PDF-Fixer-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$sha = Write-Sha256File -File $setup.FullName
Ok ("installer: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
Ok ("SHA-256: {0}  (also in {1}.sha256 - publish it with the release)" -f $sha, $setup.Name)
if ($signTemplate) {
    $sig = Get-AuthenticodeSignature $setup.FullName
    if ($sig.Status -ne 'Valid') { Die ("setup exe signature is {0}" -f $sig.Status) }
    Ok ("setup exe signed by {0}" -f $sig.SignerCertificate.Subject)
} else {
    Write-Host ""
    Write-Host "   UNSIGNED build. SmartScreen will warn when this is downloaded by a browser." -ForegroundColor Yellow
    Write-Host "   Sign it (-SignThumbprint / -SignCommand) or have IT push it with Intune/SCCM." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Done." -ForegroundColor Green
