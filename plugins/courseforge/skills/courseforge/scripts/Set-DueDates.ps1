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

  -Auto MODE (no instructor input): with -Auto (and no -DueDatesJson) the script
  derives the whole due-date table from Canvas + the manifest + the term calendar:
    - StartDate = the course start_at from GET /courses/:id (date part). If null,
      fall back to the term start via GET /courses/:id?include%5B%5D=term (the bracket
      MUST be percent-encoded as %5B%5D or Canvas 404s). If still null, stop and ask.
    - Weeks    = the max N among the manifest's "Week N" modules (the reliable length;
      Canvas end_at is the padded ACCESS-end, not the instructional end, so it is NOT
      used for length).
    - finalsEnd + breaks = from the term calendar (Get-TermCalendar.ps1) keyed off the
      start date. If the term is not in the calendar table, stop and ask.
  It then computes the table (via Compute-DueDates.ps1) and runs the same dry-run /
  -Apply path as the explicit -DueDatesJson flow. Provenance is printed.

  ENCODING (SKILL Gotcha 6): this file is pure ASCII; request bodies are sent as
  UTF-8 bytes (form-encoded), matching Push-CanvasProject.ps1.

  Usage:
    .\Set-DueDates.ps1 -ConfigPath .\canvas.config.12345.json `
        -ManifestPath .\canvas-export\project.12345.json `
        -DueDatesJson .\duedates.12345.json            # DRY-RUN (default)
    .\Set-DueDates.ps1 ... -Apply                       # actually write
    .\Set-DueDates.ps1 -ConfigPath ... -ManifestPath ... -Auto          # derive, dry-run
    .\Set-DueDates.ps1 -ConfigPath ... -ManifestPath ... -Auto -Apply   # derive + write
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$ManifestPath,
    [string]$DueDatesJson,
    [switch]$Auto,
    [string]$DueTime = '23:59',
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
    [string]$DueWeekday = 'Monday',
    [string]$TokenPath,
    [switch]$Apply,
    [switch]$WhatIf
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Resolve this script's directory (for sibling scripts). No $PSScriptRoot in param
# defaults; $MyInvocation at top-level scope is reliable here.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = (Get-Location).Path }

if (-not $Auto -and -not $DueDatesJson) {
    throw "Provide -DueDatesJson <path>, or pass -Auto to derive the table from Canvas + the manifest + the term calendar."
}

# Token resolution: explicit -TokenPath wins; otherwise canvas.token next to the
# config (the project working-directory convention).
if (-not $TokenPath) {
    $cfgDir = Split-Path -Parent (Resolve-Path $ConfigPath).Path
    $TokenPath = Join-Path $cfgDir 'canvas.token'
}

$cfg      = Get-Content -Raw -Encoding UTF8 $ConfigPath   | ConvertFrom-Json
$manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
# Explicit path loads the table from file now; -Auto derives $due below (it needs the
# live Canvas API + the term calendar). -DueDatesJson always wins if both are given.
$due      = if ($DueDatesJson) { Get-Content -Raw -Encoding UTF8 $DueDatesJson | ConvertFrom-Json } else { $null }
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

# Pull a date-only 'yyyy-MM-dd' out of a Canvas ISO timestamp (or $null).
function DatePart {
    param([string]$Iso)
    if (-not $Iso) { return $null }
    $m = [regex]::Match($Iso, '^(\d{4}-\d{2}-\d{2})')
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}

# --- -Auto: derive Start / Weeks / finals+breaks with NO instructor input --------
if ($Auto -and -not $DueDatesJson) {

    # 1) StartDate: course start_at (date part) from GET /courses/:id.
    $course   = Invoke-Canvas GET ""
    $startStr = DatePart $course.start_at
    $startSrc = "start_at from Canvas course"

    # Fall back to the term start. NOTE: the include bracket MUST be percent-encoded
    # as %5B%5D or Canvas 404s.
    if (-not $startStr) {
        $courseT = Invoke-Canvas GET "?include%5B%5D=term"
        if ($courseT.term) { $startStr = DatePart $courseT.term.start_at }
        $startSrc = "term start_at from Canvas (course start_at was null)"
    }
    if (-not $startStr) {
        throw "Could not derive a start date: course start_at and term start_at are both null. Set a course start date in Canvas, or pass dates manually via -DueDatesJson."
    }

    # 2) Weeks: the max N among the manifest's "Week N" modules (the reliable length).
    #    Canvas end_at is the padded access-end, NOT the instructional end, so we do
    #    NOT use it for length.
    $maxWeek = 0
    foreach ($m in $manifest.modules) {
        if ([string]$m.name -match '(?i)Week\s+(\d+)') {
            $wk = [int]$Matches[1]
            if ($wk -gt $maxWeek) { $maxWeek = $wk }
        }
    }
    if ($maxWeek -lt 1) {
        throw "Could not derive course length: the manifest has no 'Week N' modules. This looks like a blank shell -- pass dates manually via -DueDatesJson, or add Week modules."
    }
    $weeksSrc = "weeks from $maxWeek Week-modules"

    # 3) finals + breaks: from the term calendar keyed off the start date.
    . (Join-Path $scriptDir 'Get-TermCalendar.ps1')
    $cal = Get-TermCalendar -StartDate $startStr
    if (-not $cal) {
        $termGuess = Get-TermName -StartDate $startStr
        throw "Term '$termGuess' is not in the academic-calendar.md term table. Add it (finalsEnd + breaks), or pass dates manually via -DueDatesJson."
    }
    $calSrc = "finals/breaks from $($cal.Term) calendar"

    # 4) Compute the table (reuse Compute-DueDates.ps1 logic) and feed the apply path.
    $computePath = Join-Path $scriptDir 'Compute-DueDates.ps1'
    $due = & $computePath -StartDate $startStr -Weeks $maxWeek -FinalsEnd $cal.FinalsEnd `
        -Breaks $cal.Breaks -DueTime $DueTime -DueWeekday $DueWeekday -AsJson | ConvertFrom-Json

    Write-Host "[AUTO] derived schedule with no instructor input:"
    Write-Host ("  start  = {0}   ({1})" -f $startStr, $startSrc)
    Write-Host ("  weeks  = {0}   ({1})" -f $maxWeek, $weeksSrc)
    Write-Host ("  finals = {0} | breaks = {1}   ({2})" -f $cal.FinalsEnd, $($(if ($cal.Breaks.Count) { $cal.Breaks -join ', ' } else { '(none)' })), $calSrc)
    Write-Host ""
    Write-Host "Derived due-date table (review before applying):"
    $due | Format-Table -AutoSize Week, DueDate, DueAt | Out-Host
    Write-Host ""
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
