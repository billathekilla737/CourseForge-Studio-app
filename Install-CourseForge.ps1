<#
  Install-CourseForge.ps1
  ------------------------------------------------------------------------------
  Manual / automated installer for the garris-canvas-tools plugins.

  USE THIS when Claude Code's `/plugin` marketplace is NOT available -- e.g. the
  Claude Agent SDK harness, automation/headless runs, or any surface where typing
  `/plugin` returns "isn't available in this environment". An AI assistant can RUN
  this script for you (it cannot type `/plugin` slash commands for you).

  Unlike the retired "copy the skill folder" method, this DOES the thing a manual
  copy could not: it REGISTERS the canvas-pii-guard PreToolUse/PostToolUse hooks
  into your Claude settings.json. So the safety layer is never left off -- the
  whole reason folder-copy was retired.

  What it installs (idempotent -- safe to re-run):
    * skills  -> <config>/skills/<each skill under plugins/courseforge/skills>
    * guard   -> <config>/plugins/canvas-pii-guard
    * hooks   -> <config>/settings.json  (Pre/PostToolUse, absolute paths, merged)

  Then it runs the bundled 51-check guard test suite to prove the block is active.

  Windows / PowerShell 5.1+. Run from inside a clone of this repo:
      powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-CourseForge.ps1

  Params:
    -ConfigDir <path>  Claude config dir (default: $env:CLAUDE_CONFIG_DIR, else ~/.claude)
    -SkipTests         Skip the post-install guard test run.
#>
[CmdletBinding()]
param(
    [string]$ConfigDir = $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }),
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
function Say($m) { Write-Host "  $m" }

Write-Host "`ngarris-canvas-tools installer"
Write-Host "  repo:   $repo"
Write-Host "  config: $ConfigDir`n"

# --- sanity: this must be a repo clone with the plugins/ tree -------------------
$cfSkillsRoot = Join-Path $repo 'plugins\courseforge\skills'
$guardSrc     = Join-Path $repo 'plugins\canvas-pii-guard'
if (-not (Test-Path $cfSkillsRoot) -or -not (Test-Path $guardSrc)) {
    throw "Run this from a clone of garris-canvas-tools (expected plugins\courseforge\skills and plugins\canvas-pii-guard next to this script)."
}

# --- ensure target dirs ---------------------------------------------------------
$skillsDir  = Join-Path $ConfigDir 'skills'
$pluginsDir = Join-Path $ConfigDir 'plugins'
New-Item -ItemType Directory -Force -Path $skillsDir, $pluginsDir | Out-Null

# --- 1) install every skill under the courseforge plugin (courseforge, humanizer, ...)
Write-Host "Installing skills..."
foreach ($skill in Get-ChildItem -Directory $cfSkillsRoot) {
    $dst = Join-Path $skillsDir $skill.Name
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $skill.FullName $dst
    Say "skill: $($skill.Name)  ->  $dst"
}

# --- 2) install the canvas-pii-guard plugin (scripts + tests + docs) ------------
Write-Host "Installing canvas-pii-guard..."
$guardDst = Join-Path $pluginsDir 'canvas-pii-guard'
if (Test-Path $guardDst) { Remove-Item -Recurse -Force $guardDst }
Copy-Item -Recurse -Force $guardSrc $guardDst
Say "guard  ->  $guardDst"

# absolute paths to the INSTALLED guard scripts (forward slashes; powershell -File accepts them)
$blockPs  = ((Join-Path $guardDst 'scripts\guard-block.ps1')  -replace '\\','/')
$redactPs = ((Join-Path $guardDst 'scripts\guard-redact.ps1') -replace '\\','/')
if (-not (Test-Path $blockPs) -or -not (Test-Path $redactPs)) { throw "Guard scripts missing after copy." }

# --- 3) register hooks in settings.json (MERGE, idempotent) ---------------------
Write-Host "Registering guard hooks in settings.json..."
$settingsPath = Join-Path $ConfigDir 'settings.json'

function ConvertTo-HashtableDeep($o) {
    if ($o -is [System.Management.Automation.PSCustomObject]) {
        $h = @{}; foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = ConvertTo-HashtableDeep $p.Value }; return $h
    } elseif ($o -is [object[]]) {
        $a = @($o | ForEach-Object { ConvertTo-HashtableDeep $_ })
        return ,$a   # leading comma stops PowerShell unwrapping a 1-element array to a scalar
    } else { return $o }
}

# Minimal, correct JSON writer. PS 5.1's ConvertTo-Json unwraps single-element arrays
# of scalars (e.g. permissions.allow ["x"] -> "x"), which would corrupt a user's
# existing settings on merge. This always emits arrays as arrays.
function ConvertTo-JsonStr([string]$s) {
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        switch ($ch) {
            '"'  { [void]$sb.Append('\"') }
            '\'  { [void]$sb.Append('\\') }
            "`b" { [void]$sb.Append('\b') }
            "`f" { [void]$sb.Append('\f') }
            "`n" { [void]$sb.Append('\n') }
            "`r" { [void]$sb.Append('\r') }
            "`t" { [void]$sb.Append('\t') }
            default { if ([int]$ch -lt 32) { [void]$sb.Append(('\u{0:x4}' -f [int]$ch)) } else { [void]$sb.Append($ch) } }
        }
    }
    [void]$sb.Append('"'); return $sb.ToString()
}
function ConvertTo-JsonSafe($Value, [int]$Indent = 0) {
    $pad = '  ' * $Indent; $pad2 = '  ' * ($Indent + 1)
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [bool]) { return $(if ($Value) { 'true' } else { 'false' }) }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
        return [string]::Format([cultureinfo]::InvariantCulture, '{0}', $Value)
    }
    if ($Value -is [hashtable]) {
        if ($Value.Count -eq 0) { return '{}' }
        $items = foreach ($k in $Value.Keys) { "$pad2{0}: {1}" -f (ConvertTo-JsonStr "$k"), (ConvertTo-JsonSafe $Value[$k] ($Indent + 1)) }
        return "{`n" + ($items -join ",`n") + "`n$pad}"
    }
    if ($Value -is [object[]] -or ($Value -is [System.Collections.IList] -and $Value -isnot [string])) {
        if ($Value.Count -eq 0) { return '[]' }
        $items = foreach ($el in $Value) { "$pad2" + (ConvertTo-JsonSafe $el ($Indent + 1)) }
        return "[`n" + ($items -join ",`n") + "`n$pad]"
    }
    return (ConvertTo-JsonStr "$Value")
}

$settings = @{}
if (Test-Path $settingsPath) {
    $raw = Get-Content $settingsPath -Raw
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $parsed = $raw | ConvertFrom-Json
        $settings = ConvertTo-HashtableDeep $parsed
        if ($settings -isnot [hashtable]) { $settings = @{} }
    }
    Copy-Item $settingsPath "$settingsPath.bak" -Force   # backup before we touch it
    Say "backed up existing settings -> settings.json.bak"
}
if (-not $settings.ContainsKey('hooks') -or $settings['hooks'] -isnot [hashtable]) { $settings['hooks'] = @{} }
$hooks = $settings['hooks']

function Set-GuardEvent {
    param([hashtable]$Hooks, [string]$EventName, [string]$Command)
    if (-not $Hooks.ContainsKey($EventName) -or $Hooks[$EventName] -isnot [object[]]) { $Hooks[$EventName] = @() }
    # drop any prior guard entries so re-runs (or moved paths) don't duplicate
    $kept = @()
    foreach ($entry in $Hooks[$EventName]) {
        $isGuard = $false
        if ($entry -is [hashtable] -and $entry.ContainsKey('hooks')) {
            foreach ($h in @($entry['hooks'])) {
                if ($h -is [hashtable] -and "$($h['command'])" -match 'guard-(block|redact)\.ps1') { $isGuard = $true }
            }
        }
        if (-not $isGuard) { $kept += $entry }
    }
    $kept += @{ matcher = 'Bash|PowerShell|WebFetch'; hooks = @(@{ type = 'command'; command = $Command }) }
    $Hooks[$EventName] = @($kept)
}

$blockCmd  = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$blockPs`""
$redactCmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$redactPs`""
Set-GuardEvent -Hooks $hooks -EventName 'PreToolUse'  -Command $blockCmd
Set-GuardEvent -Hooks $hooks -EventName 'PostToolUse' -Command $redactCmd
$settings['hooks'] = $hooks

$json = ConvertTo-JsonSafe $settings
[System.IO.File]::WriteAllText($settingsPath, $json)   # UTF-8, no BOM
Say "hooks registered -> $settingsPath"

# --- 3b) optional dependency: python-pptx (powers automated PPTX ADA remediation) ---
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    & python -c "import pptx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing python-pptx (needed for PPTX ADA remediation)..."
        & python -m pip install --quiet python-pptx
        if ($LASTEXITCODE -eq 0) { Say "python-pptx installed" }
        else { Write-Host "  (could not install python-pptx - PPTX remediation will prompt for it later)" }
    } else {
        Say "python-pptx already present"
    }
} else {
    Write-Host "  (Python not found - PPTX ADA remediation needs Python + python-pptx; HTML features unaffected)"
}

# --- 4) prove it: run the bundled guard tests -----------------------------------
if (-not $SkipTests) {
    $tests = Join-Path $guardDst 'tests\Run-GuardTests.ps1'
    if (Test-Path $tests) {
        Write-Host "`nRunning guard test suite..."
        & powershell -NoProfile -ExecutionPolicy Bypass -File $tests
    }
}

Write-Host "`nDone."
Write-Host "  - In a normal Claude Code app, fully restart so hooks load."
Write-Host "  - Verify: ask Claude 'Is canvas-pii-guard active, and do you have the courseforge skill?'"
Write-Host "  - Then connect Canvas: open a course folder and say 'Set up my Canvas.'`n"
