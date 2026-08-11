<#
  bootstrap.ps1 - one-line CourseForge install. Paste into PowerShell:

    irm https://raw.githubusercontent.com/billathekilla737/CourseForge/main/bootstrap.ps1 | iex

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

$RepoSlug = 'billathekilla737/CourseForge'
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
    if ($claude) {
        # claude.exe exists but its folder is not on PATH (the CLI installer can
        # leave it off) - fix the USER PATH so plain `claude` works in NEW windows.
        $binDir = Split-Path -Parent $claude
        $userPath = [Environment]::GetEnvironmentVariable('Path','User')
        if (($userPath -split ';') -notcontains $binDir) {
            [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $binDir), 'User')
            Say "added $binDir to your PATH (new terminal windows will recognize 'claude')"
        }
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
    $repoDir = Get-ChildItem -Path $work -Directory | Where-Object { $_.Name -like 'CourseForge*' } | Select-Object -First 1
    if (-not $repoDir) { throw "Could not find the extracted repo folder under $work" }
    $installer = Join-Path $repoDir.FullName 'Install-CourseForge.ps1'
    if (-not (Test-Path $installer)) { throw "Installer missing in the downloaded repo." }
    Write-Host "Running Install-CourseForge.ps1 ..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { Warn "installer exited with code $LASTEXITCODE (see output above)" }
    else { $installed = $true }
}

# --- python-pptx (PPTX ADA remediation) - both paths --------------------------------
# Python is OPTIONAL - this block must never abort the bootstrap. Get-Command finds the
# Windows App Execution Alias stub (WindowsApps\python.exe) even when no real Python is
# installed; the stub exits non-zero with "Python was not found". And under
# $ErrorActionPreference = 'Stop' a native command with redirected stderr raises
# NativeCommandError, which would kill the run. So probe for real, and stay non-fatal.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $probe = & python --version 2>&1
        if ($LASTEXITCODE -ne 0 -or "$probe" -match 'was not found') {
            Warn "that 'python' is a Microsoft Store stub, not a real interpreter - treating Python as absent."
            Warn "install Python 3 (python.org, or: winget install Python.Python.3.12) and re-run this line."
            $py = $null
        }
    }
    if ($py) {
        & python -c "import pptx, docx, pypdf" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Installing document libraries (PPTX/DOCX remediation + PDF triage)..."
            & python -m pip install --quiet python-pptx python-docx pypdf
            if ($LASTEXITCODE -eq 0) { Say "document libraries installed" } else { Warn "could not install document libraries (those features will ask later)" }
        } else { Say "document libraries present (pptx/docx/pypdf)" }
    } else {
        Warn "Python not found - PPTX/DOCX remediation and PDF triage need Python 3; everything else works without it."
    }
} catch {
    Warn ("skipped the Python step: {0}" -f $_.Exception.Message)
} finally {
    $ErrorActionPreference = $prevEap
}

# --- done ----------------------------------------------------------------------------
Write-Host ""
if ($installed) {
    Write-Host "DONE. You will not need PowerShell again - everything else happens in the Claude Code app."
    Write-Host ""
    Write-Host "  1. FULLY restart Claude Code (quit and reopen). Approve the trust prompt if one appears."
    Write-Host "  2. In the app, use Open Folder and pick (or create) this folder:"
    # Print the REAL path. On a OneDrive-redirected machine "Documents\canvas-work" is
    # ambiguous: the Documents in File Explorer is not $env:USERPROFILE\Documents.
    $docs = ''
    try { $docs = [Environment]::GetFolderPath('MyDocuments') } catch {}
    if (-not $docs) { $docs = Join-Path $env:USERPROFILE 'Documents' }
    Write-Host ("        " + (Join-Path $docs 'canvas-work'))
    Write-Host "     That folder is simply where your Canvas connection gets saved - always open"
    Write-Host "     the same one and you stay connected."
    Write-Host "  3. Say:  set up my Canvas"
    Write-Host ""
    Write-Host "Verify any time by asking: 'Is canvas-pii-guard active, and do you have the courseforge skill?'"
} else {
    Write-Host "Install did NOT complete - review the messages above, then re-run this line."
    Write-Host "Manual fallback: download the repo ZIP from github.com/$RepoSlug and run Install-CourseForge.ps1"
}
