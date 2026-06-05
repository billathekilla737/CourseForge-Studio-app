<#
  Set-DueDates.ps1
  Apply a week -> due-date table (the output of Compute-DueDates.ps1 -AsJson) to a
  course's assignments and quizzes, using a project manifest to know which item
  belongs to which "Week N" module.

  HOW IT MAPS:
    - Each manifest module named "Week N ..." (e.g. "Week 13 -- Final Review") is
      matched to the DueAt for week N from the due-dates JSON.
    - For every Assignment / Quiz item in that module, the item is resolved to its
      live Canvas id by matching its name/title (the manifest assignment 'name' /
      quiz 'title') against GET /assignments and GET /quizzes, then the due date is
      written: assignment[due_at] for assignments, quiz[due_at] for quizzes.
    - The "Start Here" module is skipped. Page / Discussion / SubHeader items are
      skipped (no due date is written for them here).

  SAFETY: DRY-RUN by default -- it prints the item -> due_at table it WOULD write and
  changes nothing. Pass -Apply to actually PUT the dates. -WhatIf forces dry-run.

  ENCODING (SKILL Gotcha 6): this file is pure ASCII; request bodies are sent as
  UTF-8 bytes (form-encoded), matching Push-CanvasProject.ps1.

  Usage:
    .\Set-DueDates.ps1 -ConfigPath .\canvas.config.12345.json `
        -ManifestPath .\canvas-export\project.12345.json `
        -DueDatesJson .\duedates.12345.json            # DRY-RUN (default)
    .\Set-DueDates.ps1 ... -Apply                       # actually write
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$ManifestPath,
    [Parameter(Mandatory)] [string]$DueDatesJson,
    [string]$TokenPath,
    [switch]$Apply,
    [switch]$WhatIf
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Token resolution: explicit -TokenPath wins; otherwise canvas.token next to the
# config (the project working-directory convention).
if (-not $TokenPath) {
    $cfgDir = Split-Path -Parent (Resolve-Path $ConfigPath).Path
    $TokenPath = Join-Path $cfgDir 'canvas.token'
}

$cfg      = Get-Content -Raw -Encoding UTF8 $ConfigPath   | ConvertFrom-Json
$manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
$due      = Get-Content -Raw -Encoding UTF8 $DueDatesJson | ConvertFrom-Json
$token    = (Get-Content -Raw $TokenPath).Trim()
$base     = $cfg.base_url.TrimEnd('/')
$courseId = $cfg.course_id
$api      = "$base/api/v1/courses/$courseId"
$headers  = @{ Authorization = "Bearer $token" }

$dryRun = -not $Apply -or $WhatIf

# Form-encoded request (UTF-8 bytes), same shape as Push-CanvasProject.ps1.
function Invoke-Canvas {
    param([string]$Method, [string]$Path, [hashtable]$Body)
    $uri = "$api$Path"
    try {
        if ($Body) {
            $pairs = foreach ($k in $Body.Keys) { '{0}={1}' -f [uri]::EscapeDataString($k), [uri]::EscapeDataString([string]$Body[$k]) }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
            return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -Body $bytes -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -ErrorAction Stop
        }
        return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -ErrorAction Stop
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.Response) {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $msg = "$msg :: " + $sr.ReadToEnd()
        }
        throw "Canvas $Method $uri failed: $msg"
    }
}

# Build a Week-number -> DueAt lookup from the due-dates JSON.
$dueByWeek = @{}
foreach ($row in $due) { $dueByWeek[[int]$row.Week] = [string]$row.DueAt }

# Resolve manifest item -> live Canvas object by matching name/title.
# Pull the live assignment + quiz lists once (content only; carries no student PII).
$liveAssignments = Invoke-Canvas GET "/assignments?per_page=100"
$liveQuizzes     = Invoke-Canvas GET "/quizzes?per_page=100"

# Manifest lookups: item key -> the manifest record (so a module item can carry just
# a 'key' and we recover its name/title).
$mAssignByKey = @{}
foreach ($a in $manifest.assignments) { if ($a.key) { $mAssignByKey[[string]$a.key] = $a } }
$mQuizByKey = @{}
foreach ($q in $manifest.quizzes) { if ($q.key) { $mQuizByKey[[string]$q.key] = $q } }

Write-Host ("Target: {0}  ({1})" -f $manifest.course_label, $api)
Write-Host ("Due-date weeks loaded: {0}" -f $dueByWeek.Count)
if ($dryRun) { Write-Host "[DRY-RUN] no writes -- pass -Apply to set dates`n" } else { Write-Host "[APPLY] writing due dates`n" }

$plan = @()      # rows we will print (and apply)
$skipped = @()   # informational

foreach ($m in $manifest.modules) {
    $name = [string]$m.name

    # Skip "Start Here" (and anything without a "Week N" prefix -- no week => no due).
    if ($name -match '^\s*Start\s*Here') { continue }
    if ($name -notmatch '(?i)Week\s+(\d+)') { continue }
    $weekNum = [int]$Matches[1]

    if (-not $dueByWeek.ContainsKey($weekNum)) {
        $skipped += "module '$name' -> no due date for week $weekNum"
        continue
    }
    $dueAt = $dueByWeek[$weekNum]

    foreach ($it in $m.items) {
        switch ($it.type) {
            'Assignment' {
                # Recover the manifest name (item may carry only 'key').
                $aName = if ($it.name) { [string]$it.name } elseif ($it.key -and $mAssignByKey.ContainsKey([string]$it.key)) { [string]$mAssignByKey[[string]$it.key].name } else { $null }
                if (-not $aName) { $skipped += "Week $weekNum assignment item has no name/key"; break }
                $hit = $liveAssignments | Where-Object { $_.name -eq $aName } | Select-Object -First 1
                if (-not $hit) { $skipped += "Week $weekNum assignment '$aName' not found in Canvas"; break }
                $plan += [pscustomobject]@{ Week=$weekNum; Kind='Assignment'; Name=$aName; Id=$hit.id; DueAt=$dueAt }
            }
            'Quiz' {
                $qTitle = if ($it.title) { [string]$it.title } elseif ($it.key -and $mQuizByKey.ContainsKey([string]$it.key)) { [string]$mQuizByKey[[string]$it.key].title } else { $null }
                if (-not $qTitle) { $skipped += "Week $weekNum quiz item has no title/key"; break }
                $hit = $liveQuizzes | Where-Object { $_.title -eq $qTitle } | Select-Object -First 1
                if (-not $hit) { $skipped += "Week $weekNum quiz '$qTitle' not found in Canvas"; break }
                $plan += [pscustomobject]@{ Week=$weekNum; Kind='Quiz'; Name=$qTitle; Id=$hit.id; DueAt=$dueAt }
            }
            default { }   # Page / Discussion / SubHeader: no due date set here
        }
    }
}

if ($plan.Count -eq 0) {
    Write-Host "No assignment/quiz items matched a Week module with a due date."
} else {
    Write-Host "Planned due dates:"
    $plan | Format-Table -AutoSize Week, Kind, Name, Id, DueAt | Out-Host
}

if ($skipped.Count) {
    Write-Host "Skipped / not matched:"
    foreach ($s in $skipped) { Write-Host "  - $s" }
    Write-Host ""
}

if (-not $dryRun) {
    foreach ($p in $plan) {
        if ($p.Kind -eq 'Assignment') {
            Invoke-Canvas PUT "/assignments/$($p.Id)" @{ 'assignment[due_at]' = $p.DueAt } | Out-Null
        } else {
            Invoke-Canvas PUT "/quizzes/$($p.Id)" @{ 'quiz[due_at]' = $p.DueAt } | Out-Null
        }
        Write-Host ("  set {0} '{1}' (id={2}) due_at={3}" -f $p.Kind, $p.Name, $p.Id, $p.DueAt)
    }
    Write-Host ("`nDone. {0} item(s) updated." -f $plan.Count)
} else {
    Write-Host ("[DRY-RUN] {0} item(s) would be updated. Re-run with -Apply to write." -f $plan.Count)
}
