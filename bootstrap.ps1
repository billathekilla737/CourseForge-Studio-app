<#
  bootstrap.ps1 - one-line CourseForge install. Paste into PowerShell:

    irm https://raw.githubusercontent.com/billathekilla737/garris-canvas-tools/main/bootstrap.ps1 | iex

  What it does (no git, no .bat, nothing else to download):
    1. If the Claude Code CLI is available, installs both plugins the supported
       way, non-interactively:  claude plugin marketplace add + claude plugin
       install courseforge / canvas-pii-guard  (hooks register via the plugin
       system; you approve the trust prompt on next launch).
    2. Otherwise (or if that fails) downloads this repo as a ZIP and runs
       Install-CourseForge.ps1, which copies the skills, registers the guard
       hooks into settings.json, and runs the guard test suite.
    3. Either way, ensures python-pptx (powers PPTX ADA remediation) when
       Python is present.

  Safe to re-run any time (both paths are idempotent).
  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [switch]$ForceScript,          # skip the CLI path, use the ZIP + installer
    [string]$Ref = 'main'          # branch or tag to install from
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoSlug = 'billathekilla737/garris-canvas-tools'
$Marketplace = 'garris-canvas-tools'

function Say([string]$m)  { Write-Host ("  + " + $m) }
function Warn([string]$m) { Write-Host ("  ! " + $m) }

Write-Host ""
Write-Host "CourseForge bootstrap"
Write-Host "====================="

# --- locate the Claude Code CLI ---------------------------------------------------
$claude = $null
$cmd = Get-Command claude -ErrorAction SilentlyContinue
if ($cmd) { $claude = $cmd.Source }
if (-not $claude) {
    foreach ($cand in @(
        (Join-Path $env:USERPROFILE '.local\bin\claude.exe'),
        (Join-Path $env:APPDATA 'npm\claude.cmd'))) {
        if (Test-Path $cand) { $claude = $cand; break }
    }
}

$installed = $false

# --- path 1: plugin marketplace via the CLI (preferred) ---------------------------
if ($claude -and -not $ForceScript) {
    Write-Host "Claude Code CLI found: $claude"
    Write-Host "Installing plugins via the marketplace (non-interactive)..."
    try {
        & $claude plugin marketplace add $RepoSlug 2>&1 | ForEach-Object { "    $_" }
        if ($LASTEXITCODE -ne 0) { throw "marketplace add exited $LASTEXITCODE" }
        & $claude plugin install "courseforge@$Marketplace" 2>&1 | ForEach-Object { "    $_" }
        if ($LASTEXITCODE -ne 0) { throw "courseforge install exited $LASTEXITCODE" }
        & $claude plugin install "canvas-pii-guard@$Marketplace" 2>&1 | ForEach-Object { "    $_" }
        if ($LASTEXITCODE -ne 0) { throw "canvas-pii-guard install exited $LASTEXITCODE" }
        $installed = $true
        Say "both plugins installed via marketplace"
    } catch {
        Warn ("CLI plugin install failed ({0}) - falling back to the script installer." -f $_.Exception.Message)
    }
} elseif (-not $claude) {
    Write-Host "Claude Code CLI not on PATH - using the script installer."
}

# --- path 2: ZIP download + Install-CourseForge.ps1 --------------------------------
if (-not $installed) {
    $zipUrl  = "https://codeload.github.com/$RepoSlug/zip/refs/heads/$Ref"
    $work    = Join-Path $env:TEMP ("courseforge-bootstrap-" + [guid]::NewGuid().ToString('N').Substring(0,8))
    $zipPath = Join-Path $work 'repo.zip'
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Write-Host "Downloading $RepoSlug@$Ref ..."
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $work -Force
    $repoDir = Get-ChildItem -Path $work -Directory | Where-Object { $_.Name -like 'garris-canvas-tools*' } | Select-Object -First 1
    if (-not $repoDir) { throw "Could not find the extracted repo folder under $work" }
    $installer = Join-Path $repoDir.FullName 'Install-CourseForge.ps1'
    if (-not (Test-Path $installer)) { throw "Installer missing in the downloaded repo." }
    Write-Host "Running Install-CourseForge.ps1 ..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { Warn "installer exited with code $LASTEXITCODE (see output above)" }
    else { $installed = $true }
}

# --- python-pptx (PPTX ADA remediation) - both paths --------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    & python -c "import pptx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing python-pptx (PPTX ADA remediation)..."
        & python -m pip install --quiet python-pptx
        if ($LASTEXITCODE -eq 0) { Say "python-pptx installed" } else { Warn "could not install python-pptx (PPTX remediation will ask for it later)" }
    } else { Say "python-pptx present" }
} else {
    Warn "Python not found - PPTX ADA remediation needs Python 3 + python-pptx; everything else works without it."
}

# --- done ----------------------------------------------------------------------------
Write-Host ""
if ($installed) {
    Write-Host "DONE. Two steps left:"
    Write-Host "  1. FULLY restart Claude Code (quit and reopen) so the skills and safety hooks load."
    Write-Host "     Approve the plugin trust prompt if one appears."
    Write-Host "  2. Open your course folder in Claude Code and say:  set up my Canvas"
    Write-Host ""
    Write-Host "Verify any time by asking: 'Is canvas-pii-guard active, and do you have the courseforge skill?'"
} else {
    Write-Host "Install did NOT complete - review the messages above, then re-run this line."
    Write-Host "Manual fallback: download the repo ZIP from github.com/$RepoSlug and run Install-CourseForge.ps1"
}
