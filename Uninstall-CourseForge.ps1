<#
  Uninstall-CourseForge.ps1 - clean removal of both plugins.

  Removes (from $env:CLAUDE_CONFIG_DIR or ~/.claude):
    - the courseforge + humanizer skills (skills\)
    - the canvas-pii-guard plugin folder (plugins\canvas-pii-guard)
    - the guard's PreToolUse/PostToolUse hook entries from settings.json
      (other hooks and every other setting are preserved; a backup is written
      to settings.json.uninstall.bak first)
    - best-effort: `claude plugin uninstall` both + marketplace remove, when
      the Claude Code CLI is available (harmless if it is not)

  DOES NOT touch your course data: canvas.token, canvas.config.*.json,
  Documents\canvas-work, exports, or any remediation work folders.

  Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File .\Uninstall-CourseForge.ps1

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [string]$ConfigDir = $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' })
)

$ErrorActionPreference = 'Stop'
function Say([string]$m)  { Write-Host ("  + " + $m) }
function Warn([string]$m) { Write-Host ("  ! " + $m) }

Write-Host ""
Write-Host "CourseForge uninstall  (config dir: $ConfigDir)"
Write-Host "===================================================="

# --- 1) best-effort CLI uninstall (marketplace installs) ---------------------------
$cmd = Get-Command claude -ErrorAction SilentlyContinue
if ($cmd) {
    foreach ($p in @('courseforge@garris-canvas-tools','canvas-pii-guard@garris-canvas-tools')) {
        try { & $cmd.Source plugin uninstall $p 2>&1 | Out-Null; Say "claude plugin uninstall $p" } catch { }
    }
    try { & $cmd.Source plugin marketplace remove garris-canvas-tools 2>&1 | Out-Null; Say "marketplace entry removed" } catch { }
}

# --- 2) remove skill + plugin folders (script installs) ----------------------------
foreach ($rel in @('skills\courseforge','skills\humanizer','plugins\canvas-pii-guard')) {
    $p = Join-Path $ConfigDir $rel
    if (Test-Path $p) { Remove-Item -Recurse -Force $p; Say "removed $rel" }
}

# --- 3) de-register guard hooks from settings.json ---------------------------------
$settingsPath = Join-Path $ConfigDir 'settings.json'
if (Test-Path $settingsPath) {

    function ConvertTo-HashtableDeep($o) {
        if ($o -is [System.Management.Automation.PSCustomObject]) {
            $h = @{}; foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = ConvertTo-HashtableDeep $p.Value }; return $h
        } elseif ($o -is [object[]]) {
            $a = @($o | ForEach-Object { ConvertTo-HashtableDeep $_ })
            return ,$a
        } else { return $o }
    }
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

    $raw = Get-Content $settingsPath -Raw
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        Copy-Item $settingsPath "$settingsPath.uninstall.bak" -Force
        Say "backed up settings -> settings.json.uninstall.bak"
        $settings = ConvertTo-HashtableDeep ($raw | ConvertFrom-Json)
        $removed = 0
        if ($settings -is [hashtable] -and $settings.ContainsKey('hooks') -and $settings['hooks'] -is [hashtable]) {
            $hooks = $settings['hooks']
            foreach ($ev in @($hooks.Keys)) {
                if ($hooks[$ev] -isnot [object[]]) { continue }
                $kept = @()
                foreach ($entry in $hooks[$ev]) {
                    $isGuard = $false
                    if ($entry -is [hashtable] -and $entry.ContainsKey('hooks')) {
                        foreach ($h in @($entry['hooks'])) {
                            if ($h -is [hashtable] -and "$($h['command'])" -match 'guard-(block|redact)\.ps1') { $isGuard = $true }
                        }
                    }
                    if ($isGuard) { $removed++ } else { $kept += $entry }
                }
                if ($kept.Count -gt 0) { $hooks[$ev] = @($kept) } else { $hooks.Remove($ev) }
            }
            if ($hooks.Count -eq 0) { $settings.Remove('hooks') }
        }
        if ($removed -gt 0) {
            [IO.File]::WriteAllText($settingsPath, (ConvertTo-JsonSafe $settings))
            Say "removed $removed guard hook entr$(if ($removed -eq 1) { 'y' } else { 'ies' }) from settings.json"
        } else {
            Say "no guard hook entries found in settings.json (nothing to remove)"
        }
    }
} else {
    Say "no settings.json found (nothing to de-register)"
}

Write-Host ""
Write-Host "DONE. Fully restart Claude Code so the removal takes effect."
Write-Host "Your Canvas tokens/configs (Documents\canvas-work etc.) were NOT touched - delete them"
Write-Host "yourself if you no longer need them."
