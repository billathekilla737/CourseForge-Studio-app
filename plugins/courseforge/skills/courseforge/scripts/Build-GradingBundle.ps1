<#
  Build-GradingBundle.ps1  (courseforge - OPT-IN blind/pseudonymized grading)
  A sterilizing + PSEUDONYMIZING gateway. It is the safe way to bring submission
  TEXT into a grading session WITHOUT bringing student identities along. It mirrors
  Get-CanvasData-Sterilized.ps1: the raw identity data stays LOCAL, only a scrubbed,
  tokenized bundle is meant to be read by the model.

  What it does:
    1. GETs the assignment's submissions (with user) from Canvas, paginated.
    2. Assigns each student a STABLE pseudonym S-001, S-002, ... (sorted by user id).
    3. Writes a LOCAL identity map  OutDir\map.json   { "S-001": {user_id,name,login_id} }
       -> this is the ONLY place identities live; it is gitignored and must NEVER be
          read into the model or committed.
    4. Writes a SCRUBBED bundle    OutDir\bundle.json [ {id:"S-001", text:<scrubbed>, files:[names]} ]
       -> the submission body is run through a self-contained PII redactor AND each
          student's own name tokens are replaced with [NAME]. This is the ONLY file
          that should be read for grading.

  IMPORTANT - this is BEST-EFFORT de-identification, not a guarantee. Free-text PII
  an unusual name in prose, a name baked into a screenshot/uploaded file, file
  metadata) can survive. Attachment CONTENTS are never downloaded or inlined - only
  filenames are listed - and screenshots/files may show names (Windows title bars,
  email headers) and must be reviewed LOCALLY, not sent to the model.

  This script is a SANCTIONED gateway: the canvas-pii-guard permits it by name even
  though it touches /submissions. Ad-hoc submission calls remain blocked.

  Usage:
    .\Build-GradingBundle.ps1 -ConfigPath ..\canvas.config.<id>.json -AssignmentId 67890
    # then read ONLY <OutDir>\bundle.json to grade by pseudonym.
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [int]$AssignmentId,
    [string]$TokenPath,
    [string]$OutDir
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Resolve paths in the body (do NOT use $PSScriptRoot in a param default).
$cfgDir = Split-Path $ConfigPath -Parent
if (-not $TokenPath) { $TokenPath = Join-Path $cfgDir 'canvas.token' }
if (-not $OutDir)    { $OutDir    = Join-Path $cfgDir ("grading\{0}" -f $AssignmentId) }

$cfg       = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
$token     = (Get-Content -Raw $TokenPath).Trim()
$base      = $cfg.base_url.TrimEnd('/')
$courseId  = $cfg.course_id
$api       = "$base/api/v1/courses/$courseId"
$headers   = @{ Authorization = "Bearer $token" }
$auditPath = Join-Path $cfgDir 'canvas-admin-audit.log'

function Audit([string]$msg) {
    $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    Add-Content -Path $auditPath -Value ("{0} user={1} {2}" -f $stamp, $env:USERNAME, $msg)
}

# --- self-contained PII redactor -------------------------------------------------
# Copied from canvas-pii-guard\scripts\PiiPatterns.ps1 (Invoke-PiiRedaction) so this
# gateway works even if the guard plugin is not installed. Keep these in sync.
function Remove-PiiLocal {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $s = $Text
    $patterns = New-Object System.Collections.ArrayList
    [void]$patterns.Add(@{ rx = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'; rep = '[EMAIL]' })
    [void]$patterns.Add(@{ rx = '\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'; rep = '[PHONE]' })
    # MGCCC ID formats (longer/more specific first).
    [void]$patterns.Add(@{ rx = '\b\d{3}\.[A-Za-z]\d{8,9}\b'; rep = '[SISID]' })   # sis_user_id: ###.X########
    [void]$patterns.Add(@{ rx = '\b[A-Za-z]\d{8,9}\b'; rep = '[USERID]' })          # login_id: letter + 8 digits
    [void]$patterns.Add(@{ rx = '\b\d{9}\b'; rep = '[ID9]' })
    foreach ($p in $patterns) { $s = [regex]::Replace($s, $p.rx, $p.rep) }
    return $s
}

# Replace a student's own name tokens with [NAME]. Splits the roster name into
# word tokens (and the sortable "Last, First" form), redacts each token >= 2 chars,
# case-insensitively, on word boundaries.
function Remove-OwnName {
    param([string]$Text, [string[]]$Names)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $s = $Text
    $tokens = New-Object System.Collections.Generic.HashSet[string]
    foreach ($n in $Names) {
        if ([string]::IsNullOrWhiteSpace($n)) { continue }
        foreach ($t in ($n -split '[\s,]+')) {
            $t = $t.Trim()
            if ($t.Length -ge 2) { [void]$tokens.Add($t) }
        }
    }
    # longest tokens first so multi-part names collapse cleanly
    foreach ($t in ($tokens | Sort-Object -Property Length -Descending)) {
        $rx = '(?i)\b' + [regex]::Escape($t) + '\b'
        $s  = [regex]::Replace($s, $rx, '[NAME]')
    }
    return $s
}

# --- fetch submissions (paginated) ----------------------------------------------
function Get-AllSubmissions {
    $all = New-Object System.Collections.ArrayList
    $uri = "$api/assignments/$AssignmentId/submissions?include[]=user&per_page=100"
    while ($uri) {
        $resp = Invoke-WebRequest -Uri $uri -Headers $headers -Method GET -UseBasicParsing -ErrorAction Stop
        $page = $resp.Content | ConvertFrom-Json
        foreach ($s in $page) { [void]$all.Add($s) }
        # follow RFC5988 Link header rel="next"
        $next = $null
        $link = $resp.Headers['Link']
        if ($link) {
            foreach ($part in ($link -split ',')) {
                if ($part -match '<([^>]+)>\s*;\s*rel="next"') { $next = $matches[1]; break }
            }
        }
        $uri = $next
    }
    return $all
}

# --- live-course warning ---------------------------------------------------------
$course = Invoke-RestMethod "$api`?include[]=total_students" -Headers $headers -Method GET -ErrorAction Stop
$asg    = Invoke-RestMethod "$api/assignments/$AssignmentId" -Headers $headers -Method GET -ErrorAction Stop
Write-Host "Course:     [$courseId] $($course.name)  (total_students=$($course.total_students), workflow_state=$($course.workflow_state))"
Write-Host "Assignment: [$AssignmentId] $($asg.name)  (out of $($asg.points_possible) pts)"

$live = ($course.workflow_state -eq 'available') -or ([int]$course.total_students -gt 0)
if ($live) {
    Write-Host ""
    Write-Host "*** WARNING: LIVE / REAL-DATA COURSE ***"
    Write-Host "*** Published and/or $($course.total_students) enrolled student(s). Real submissions will be fetched."
    Write-Host "*** Identities stay LOCAL in map.json; only the scrubbed bundle.json is meant for grading."
}
Write-Host ""

$subs = Get-AllSubmissions
# keep only real student submissions (drop the test student / placeholders with no user id)
$subs = @($subs | Where-Object { $_.user -and $_.user.id })
$subs = @($subs | Sort-Object { [int]$_.user.id })
Write-Host ("Submissions fetched: {0}" -f $subs.Count)

New-Item -ItemType Directory -Force $OutDir | Out-Null

$map    = [ordered]@{}
$bundle = New-Object System.Collections.ArrayList
$i = 0
foreach ($s in $subs) {
    $i++
    $pseud = 'S-{0:D3}' -f $i
    $u = $s.user
    $map[$pseud] = [ordered]@{
        user_id  = $u.id
        name     = $u.name
        login_id = $u.login_id
    }

    # collect the student's name forms to scrub from their own text
    $nameForms = @($u.name, $u.short_name, $u.sortable_name) | Where-Object { $_ }

    $body = ''
    if ($s.body) { $body = [string]$s.body }
    $scrubbed = Remove-PiiLocal (Remove-OwnName $body $nameForms)

    # filenames only - never download or inline attachment/file/image contents
    $files = @()
    if ($s.attachments) { $files = @($s.attachments | ForEach-Object { $_.display_name }) }

    [void]$bundle.Add([ordered]@{
        id    = $pseud
        text  = $scrubbed
        files = $files
    })
}

$mapPath    = Join-Path $OutDir 'map.json'
$bundlePath = Join-Path $OutDir 'bundle.json'
[IO.File]::WriteAllText($mapPath,    (($map    | ConvertTo-Json -Depth 6)))
[IO.File]::WriteAllText($bundlePath, (@($bundle) | ConvertTo-Json -Depth 6))

Audit ("action=build-grading-bundle course=$courseId('$($course.name)') assignment=$AssignmentId students=$($subs.Count) live=$live")

Write-Host ""
Write-Host "Wrote LOCAL identity map (gitignored, NEVER read by the model / committed):"
Write-Host "    $mapPath"
Write-Host "Wrote SCRUBBED bundle (read ONLY this for grading):"
Write-Host "    $bundlePath"
Write-Host ""
Write-Host "NOTE: de-identification is BEST-EFFORT, not a guarantee. Free-text PII may remain."
Write-Host "NOTE: attachment/file CONTENTS were NOT downloaded - only filenames are listed."
Write-Host "      Screenshots and uploaded files may contain names (Windows title bars, email"
Write-Host "      headers, signatures) and must be reviewed LOCALLY, not sent to the model."
Write-Host ""
Write-Host "Grade by pseudonym, write proposed-grades.json [ {id,score,comment} ], then run Post-Grades.ps1."
Write-Host "Audit: $auditPath"