<#
  Post-Grades.ps1  (courseforge - OPT-IN blind/pseudonymized grading)
  The APPLY half of the blind-grading workflow. It mirrors the admin Grade-Submissions
  poster but is PSEUDONYM-AWARE: you grade by pseudonym (S-001, S-002, ...) and this
  script resolves each pseudonym back to a real user_id LOCALLY via map.json, then posts.

  DRY-RUN by default: it prints what it WOULD post and writes nothing unless you pass
  -Apply. It refuses if any graded pseudonym is missing from the map (so you cannot post
  to the wrong student). It prints a LIVE-COURSE warning and appends every apply to
  canvas-admin-audit.log.

  Inputs (both produced next to the config, under grading\<AssignmentId>\):
    map.json             { "S-001": { user_id, name, login_id }, ... }   (LOCAL, gitignored)
    proposed-grades.json [ { "id":"S-001", "score":88, "comment":"..." }, ... ]

  Usage:
    .\Post-Grades.ps1 -ConfigPath ..\canvas.config.<id>.json -AssignmentId 67890
    # review the dry-run output, then:
    .\Post-Grades.ps1 -ConfigPath ..\canvas.config.<id>.json -AssignmentId 67890 -Apply
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [int]$AssignmentId,
    [string]$TokenPath,
    [string]$OutDir,
    [switch]$Apply
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Resolve paths in the body (do NOT use $PSScriptRoot in a param default).
$cfgDir = Split-Path $ConfigPath -Parent
if (-not $TokenPath) { $TokenPath = Join-Path $cfgDir 'canvas.token' }
if (-not $OutDir)    { $OutDir    = Join-Path $cfgDir ("grading\{0}" -f $AssignmentId) }

$mapPath      = Join-Path $OutDir 'map.json'
$proposedPath = Join-Path $OutDir 'proposed-grades.json'
if (-not (Test-Path $mapPath))      { throw "map.json not found at $mapPath. Run Build-GradingBundle.ps1 first." }
if (-not (Test-Path $proposedPath)) { throw "proposed-grades.json not found at $proposedPath. Grade bundle.json by pseudonym first." }

$cfg       = Get-Content -Raw -Encoding UTF8 $ConfigPath   | ConvertFrom-Json
$mapRaw    = Get-Content -Raw -Encoding UTF8 $mapPath       | ConvertFrom-Json
$proposed  = Get-Content -Raw -Encoding UTF8 $proposedPath  | ConvertFrom-Json
$token     = (Get-Content -Raw $TokenPath).Trim()
$base      = $cfg.base_url.TrimEnd('/')
$courseId  = $cfg.course_id
$api       = "$base/api/v1/courses/$courseId"
$headers   = @{ Authorization = "Bearer $token" }
$auditPath = Join-Path $cfgDir 'canvas-admin-audit.log'

# rehydrate the map into a hashtable: pseudonym -> user_id
$map = @{}
foreach ($p in $mapRaw.PSObject.Properties) { $map[$p.Name] = $p.Value.user_id }

function Post-Canvas($path, $body) {
    $pairs = foreach ($k in $body.Keys) { '{0}={1}' -f [uri]::EscapeDataString($k), [uri]::EscapeDataString([string]$body[$k]) }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
    Invoke-RestMethod -Uri "$api$path" -Headers $headers -Method PUT -Body $bytes -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -ErrorAction Stop
}
function Audit([string]$msg) {
    $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    Add-Content -Path $auditPath -Value ("{0} user={1} {2}" -f $stamp, $env:USERNAME, $msg)
}

# --- refuse if any graded pseudonym is missing from the map ----------------------
$missing = @()
foreach ($g in $proposed) {
    if (-not $g.id)            { $missing += '(blank id)'; continue }
    if (-not $map.ContainsKey($g.id)) { $missing += $g.id }
}
if ($missing.Count -gt 0) {
    throw ("Refusing: these pseudonym(s) are not in map.json: {0}. Re-run Build-GradingBundle.ps1 or fix proposed-grades.json." -f ($missing -join ', '))
}

$course = Invoke-RestMethod "$api`?include[]=total_students" -Headers $headers -Method GET -ErrorAction Stop
$asg    = Invoke-RestMethod "$api/assignments/$AssignmentId" -Headers $headers -Method GET -ErrorAction Stop
Write-Host "Course:     [$courseId] $($course.name)  (total_students=$($course.total_students), workflow_state=$($course.workflow_state))"
Write-Host "Assignment: [$AssignmentId] $($asg.name)  (out of $($asg.points_possible) pts)"
Write-Host ("Grades in file: {0}" -f @($proposed).Count)

$apply = $Apply.IsPresent
$live  = ($course.workflow_state -eq 'available') -or ([int]$course.total_students -gt 0)
if ($apply -and $live) {
    Write-Host ""
    Write-Host "*** WARNING: LIVE / REAL-DATA COURSE ***"
    Write-Host "*** Published and/or $($course.total_students) enrolled student(s). You are about to POST grades to REAL students."
}
Write-Host ($(if ($apply) { "MODE: APPLY (posting grades)" } else { "MODE: DRY-RUN (no writes; pass -Apply to post)" }))
Write-Host ""

$ok = 0; $fail = 0
foreach ($g in $proposed) {
    $uid  = $map[$g.id]
    $clen = if ($g.comment) { ([string]$g.comment).Length } else { 0 }
    if (-not $apply) {
        Write-Host ("  [dry-run] would post {0} -> user {1}: score={2}, comment={3} chars" -f $g.id, $uid, $g.score, $clen)
        continue
    }
    $body = @{ 'submission[posted_grade]' = [string]$g.score }
    if ($g.comment) { $body['comment[text_comment]'] = [string]$g.comment }
    try {
        Post-Canvas "/assignments/$AssignmentId/submissions/$uid" $body | Out-Null
        $ok++
        Write-Host ("  posted {0} -> user {1}: {2}" -f $g.id, $uid, $g.score)
    } catch {
        $fail++
        Write-Host ("  ! {0} (user {1}) failed: {2}" -f $g.id, $uid, $_.Exception.Message)
    }
}
Write-Host ""
if ($apply) {
    Audit ("action=post-grades course=$courseId('$($course.name)') assignment=$AssignmentId posted=$ok failed=$fail live=$live")
    Write-Host "Done. posted=$ok failed=$fail"
    Write-Host "Audit: $auditPath"
} else {
    Write-Host "Dry-run complete. Re-run with -Apply to post."
}