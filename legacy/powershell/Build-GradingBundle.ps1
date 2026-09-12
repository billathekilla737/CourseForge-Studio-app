<#
  Build-GradingBundle.ps1  (courseforge - OPT-IN blind/pseudonymized grading)
  A sterilizing + PSEUDONYMIZING gateway. It is the safe way to bring submission
  TEXT into a grading session WITHOUT bringing student identities along. Raw
  identity data stays LOCAL; only a scrubbed, tokenized bundle is meant to be
  read by the model.

  What it does:
    1. GETs the assignment's submissions (with user) from Canvas, paginated.
    2. Assigns each student a STABLE pseudonym S-001, S-002, ... (sorted by user id).
    3. Writes a LOCAL identity map  OutDir\map.json   { "S-001": {user_id,name,login_id} }
       -> the ONLY place identities live; gitignored; NEVER read into the model.
    4. Writes a SCRUBBED bundle    OutDir\bundle.json - the ONLY file to grade from.

  Scrubbing (in order, per submission):
    a. EVERY roster student's full-name forms ("First Last", "Last, First",
       multi-word short names) -> [NAME], across every submission - so a peer
       mention ("I worked with Bob Smith") is caught, not just the author.
    b. The AUTHOR's individual name tokens and login_id -> [NAME] (their own
       first name alone is likelier in their own text than anyone else's).
       Other students' single first names are NOT redacted (too many collide
       with common words); full-name forms are the peer-mention signal.
    c. Structured PII -> tokens: email, phone, SSN-shaped, MGCCC sis/login ids,
       and BARE 8-10 DIGIT RUNS -> [ID] (a bare student id like 12345678 is
       exactly what students type; see -KeepLongNumbers).
    d. VERIFY: the finished bundle is re-scanned (with the canvas-pii-guard
       redactor too, when installed - an independent pattern set). Residual
       hits FAIL the build (exit 2) unless -Force.

  -IncludeAttachmentText (opt-in): downloads .docx/.pdf/.txt attachments to
  OutDir\attachments\ (LOCAL, gitignored), extracts their text, and scrubs it
  through the same pipeline into the bundle - this is what makes file-upload
  assignments gradeable. Images and every other type stay filename-only ON
  PURPOSE: screenshots carry names in title bars/headers and no text scrubber
  sees pixels. Scanned PDFs yield little text and are reported as such.

  -KeepLongNumbers: skip rule (c)'s bare 8-10 digit redaction for numeric-heavy
  work (math/CS answers like 16777216 would otherwise become [ID]). Bare 9-digit
  runs are still redacted even then. The verify step relaxes to match.

  IMPORTANT - this is BEST-EFFORT de-identification, not a guarantee. An unusual
  name in prose, a nickname the roster does not know, or identifying CONTENT
  ("I'm the only left-handed pitcher on the team") can survive any scrubber.

  This script is a SANCTIONED gateway: canvas-pii-guard permits it by name even
  though it touches /submissions. Ad-hoc submission calls remain blocked.

  Usage:
    .\Build-GradingBundle.ps1 -ConfigPath ..\canvas.config.<id>.json -AssignmentId 67890
    .\Build-GradingBundle.ps1 -ConfigPath ... -AssignmentId 67890 -IncludeAttachmentText
    # then read ONLY <OutDir>\bundle.json to grade by pseudonym.
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [int]$AssignmentId,
    [string]$TokenPath,
    [string]$OutDir,
    [switch]$IncludeAttachmentText,
    [switch]$KeepLongNumbers,
    [switch]$Force
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasToken.ps1"

# Resolve paths in the body (do NOT use $PSScriptRoot in a param default).
$cfgDir = Split-Path $ConfigPath -Parent
# $TokenPath stays empty unless the caller gave one: Get-CanvasToken then
# discovers canvas.token.enc in $cfgDir (migrating a legacy plaintext
# canvas.token into it), so no plaintext path is assumed here.
if (-not $OutDir)    { $OutDir    = Join-Path $cfgDir ("grading\{0}" -f $AssignmentId) }

$cfg       = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
$token     = (Get-CanvasToken -TokenPath $TokenPath -Dir $cfgDir).Token
$base      = $cfg.base_url.TrimEnd('/')
$courseId  = $cfg.course_id
$api       = "$base/api/v1/courses/$courseId"
$headers   = @{ Authorization = "Bearer $token" }
$auditPath = Join-Path $cfgDir 'canvas-admin-audit.log'
$extractPy = Join-Path $PSScriptRoot 'extract_attachment_text.py'

function Audit([string]$msg) {
    $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    Add-Content -Path $auditPath -Value ("{0} user={1} {2}" -f $stamp, $env:USERNAME, $msg)
}

# --- self-contained PII redactor -------------------------------------------------
# Kept in sync by hand with canvas-pii-guard\scripts\PiiPatterns.ps1 so this gateway
# works without the plugin; the verify step below uses the plugin's own copy when
# present, so the two implementations cross-check each other instead of drifting
# silently.
function Remove-PiiLocal {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $s = $Text
    $patterns = New-Object System.Collections.ArrayList
    [void]$patterns.Add(@{ rx = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'; rep = '[EMAIL]' })
    [void]$patterns.Add(@{ rx = '\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'; rep = '[PHONE]' })
    [void]$patterns.Add(@{ rx = '\b\d{3}-\d{2}-\d{4}\b'; rep = '[SSN]' })
    # MGCCC ID formats (longer/more specific first).
    [void]$patterns.Add(@{ rx = '\b\d{3}\.[A-Za-z]\d{8,9}\b'; rep = '[SISID]' })   # sis_user_id: ###.X########
    [void]$patterns.Add(@{ rx = '\b[A-Za-z]\d{8,9}\b'; rep = '[USERID]' })          # login_id: letter + 8 digits
    if ($KeepLongNumbers) {
        # numeric-heavy assignment: keep 8/10-digit values, still drop bare 9-digit
        [void]$patterns.Add(@{ rx = '\b\d{9}\b'; rep = '[ID]' })
    } else {
        # a bare 8-digit run is exactly the MGCCC student-id shape (e.g. 12345678);
        # this deliberately over-redacts long numbers in prose - see -KeepLongNumbers
        [void]$patterns.Add(@{ rx = '\b\d{8,10}\b'; rep = '[ID]' })
    }
    foreach ($p in $patterns) { $s = [regex]::Replace($s, $p.rx, $p.rep) }
    return $s
}

# Redact names. Two tiers, per the header:
#   full-name forms of EVERY roster student (peer mentions), then
#   every individual token (>=2 chars) + login_id of the AUTHOR.
function Remove-Names {
    param([string]$Text, [string[]]$RosterFullForms, [string[]]$OwnNames)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $s = $Text
    foreach ($form in ($RosterFullForms | Sort-Object -Property Length -Descending)) {
        if ([string]::IsNullOrWhiteSpace($form)) { continue }
        $rx = '(?i)' + [regex]::Escape($form)
        $s  = [regex]::Replace($s, $rx, '[NAME]')
    }
    $tokens = New-Object System.Collections.Generic.HashSet[string]
    foreach ($n in $OwnNames) {
        if ([string]::IsNullOrWhiteSpace($n)) { continue }
        foreach ($t in ($n -split '[\s,]+')) {
            $t = $t.Trim()
            if ($t.Length -ge 2) { [void]$tokens.Add($t) }
        }
    }
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
$subs = @($subs | Where-Object { $_.user -and $_.user.id })
$subs = @($subs | Sort-Object { [int]$_.user.id })
Write-Host ("Submissions fetched: {0}" -f $subs.Count)

New-Item -ItemType Directory -Force $OutDir | Out-Null

# --- roster-wide full-name forms (built once, applied to every submission) --------
$rosterForms = New-Object System.Collections.Generic.HashSet[string]
foreach ($s in $subs) {
    foreach ($form in @($s.user.name, $s.user.short_name, $s.user.sortable_name)) {
        if ([string]::IsNullOrWhiteSpace($form)) { continue }
        # only multi-word/comma forms roster-wide; single first names collide with prose
        if ($form -match '[\s,]') { [void]$rosterForms.Add($form.Trim()) }
    }
}
$rosterForms = @($rosterForms)

$attachExt = @('.docx', '.pdf', '.txt')
$pythonOk  = $false
if ($IncludeAttachmentText) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $probe = & python --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$probe" -notmatch 'was not found') { $pythonOk = $true }
    }
    if (-not $pythonOk) {
        Write-Host "!! -IncludeAttachmentText requested but no working Python 3 - attachments stay filename-only."
    }
}

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

    # the author's own tokens: all name forms + login_id
    $ownForms = @($u.name, $u.short_name, $u.sortable_name, $u.login_id) | Where-Object { $_ }

    $body = ''
    if ($s.body) { $body = [string]$s.body }
    # ORDER MATTERS: structured PII first, names second. The other way round, the
    # author's name tokens get replaced INSIDE their own email address
    # (jane.doe@... -> [NAME].[NAME]@...), and the mangled string no longer
    # matches the email pattern - leaking the domain. Found by test, kept fixed.
    $scrubbed = Remove-Names (Remove-PiiLocal $body) $rosterForms $ownForms

    $files    = @()
    $extracts = @()
    if ($s.attachments) {
        foreach ($a in $s.attachments) {
            # students name files after themselves (SmithJane_Essay.docx): the
            # bundle and the local copies carry a pseudonymous name instead
            $ext = [IO.Path]::GetExtension($a.display_name).ToLower()
            $safeName = ('{0}_file{1}{2}' -f $pseud, ($files.Count + 1), $ext)
            $files += $safeName
            if (-not ($IncludeAttachmentText -and $pythonOk)) { continue }
            if ($attachExt -notcontains $ext) { continue }
            $dlDir = Join-Path $OutDir ("attachments\{0}" -f $pseud)
            New-Item -ItemType Directory -Force $dlDir | Out-Null
            $local = Join-Path $dlDir $safeName
            try {
                Invoke-WebRequest -Uri $a.url -OutFile $local -UseBasicParsing -ErrorAction Stop
            } catch {
                $extracts += [ordered]@{ file = $safeName; note = 'download failed'; text = '' }
                continue
            }
            $raw = & python $extractPy $local 2>&1
            $txt = (@($raw | Where-Object { $_ -is [string] }) -join "`n")
            if ($LASTEXITCODE -eq 0 -and $txt.Trim()) {
                # same order as the body: structured PII first, then names
                $clean = Remove-Names (Remove-PiiLocal $txt) $rosterForms $ownForms
                $extracts += [ordered]@{ file = $safeName; note = 'text extracted + scrubbed'; text = $clean }
            } elseif ($LASTEXITCODE -eq 2) {
                $extracts += [ordered]@{ file = $safeName; note = 'no extractable text (scanned/empty) - review locally'; text = '' }
            } else {
                $extracts += [ordered]@{ file = $safeName; note = 'unsupported type - review locally'; text = '' }
            }
        }
    }

    $entry = [ordered]@{ id = $pseud; text = $scrubbed; files = $files }
    if ($extracts.Count -gt 0) { $entry['attachments_text'] = $extracts }
    [void]$bundle.Add($entry)
}

$mapPath    = Join-Path $OutDir 'map.json'
$bundlePath = Join-Path $OutDir 'bundle.json'
[IO.File]::WriteAllText($mapPath,    (($map    | ConvertTo-Json -Depth 6)))
[IO.File]::WriteAllText($bundlePath, (@($bundle) | ConvertTo-Json -Depth 8))

# --- VERIFY the finished bundle before declaring it safe to read -------------------
# Re-scan bundle.json with (1) the guard's own redactor when installed - an
# independent pattern set, so builder and guard cross-check each other - and
# (2) a local residual check for the exact shapes this script promises to remove.
$problems = New-Object System.Collections.ArrayList
$bundleRaw = Get-Content -Raw -Encoding UTF8 $bundlePath

$guardPatterns = Join-Path $HOME '.claude\plugins\canvas-pii-guard\scripts\PiiPatterns.ps1'
if (Test-Path $guardPatterns) {
    . $guardPatterns
    $g = Invoke-PiiRedaction -Text $bundleRaw -Profile 'Standard'
    if ($g.Count -gt 0) { [void]$problems.Add(("guard redactor found {0} residual structured-PII pattern(s)" -f $g.Count)) }
} else {
    Write-Host "note: canvas-pii-guard not installed - skipping independent verification pass."
}
$digitRx = if ($KeepLongNumbers) { '\b\d{9}\b' } else { '\b\d{8,10}\b' }
# exclude the pseudonyms' own digits (S-001) and json numerics like scores: check text fields only
$textBlob = (@($bundle) | ForEach-Object { @($_.text) + @($_.files) + @(($_.attachments_text | ForEach-Object { $_.text })) }) -join ' '
$digitHits = [regex]::Matches($textBlob, $digitRx).Count
if ($digitHits -gt 0) { [void]$problems.Add(("{0} bare digit run(s) matching {1} survived scrubbing" -f $digitHits, $digitRx)) }

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "!! VERIFY FAILED - the bundle may still carry identifying data:"
    foreach ($p in $problems) { Write-Host ("     " + $p) }
    if (-not $Force) {
        Write-Host "!! Refusing to bless this bundle. Inspect $bundlePath LOCALLY, fix, or re-run with -Force to accept."
        Audit ("action=build-grading-bundle VERIFY-FAILED course=$courseId assignment=$AssignmentId students=$($subs.Count)")
        exit 2
    }
    Write-Host "!! -Force given: proceeding despite residual hits. Review the bundle locally before grading."
}

Audit ("action=build-grading-bundle course=$courseId('$($course.name)') assignment=$AssignmentId students=$($subs.Count) live=$live attachText=$($IncludeAttachmentText -and $pythonOk) keepNums=$([bool]$KeepLongNumbers)")

Write-Host ""
Write-Host "Wrote LOCAL identity map (gitignored, NEVER read by the model / committed):"
Write-Host "    $mapPath"
Write-Host "Wrote SCRUBBED bundle (read ONLY this for grading):"
Write-Host "    $bundlePath"
Write-Host ""
Write-Host "Scrubbing applied: roster-wide full names, author name tokens + login_id,"
Write-Host "email/phone/SSN/id formats, bare 8-10 digit runs$(if($KeepLongNumbers){' (RELAXED: 9-digit only, -KeepLongNumbers)'}) - then verified."
Write-Host "NOTE: de-identification is BEST-EFFORT, not a guarantee. Identifying CONTENT"
Write-Host "      (a story only one student could tell) survives any scrubber."
if ($IncludeAttachmentText -and $pythonOk) {
    Write-Host "NOTE: docx/pdf/txt attachment TEXT was extracted and scrubbed into the bundle."
    Write-Host "      Images and other types remain filename-only - review those LOCALLY."
} else {
    Write-Host "NOTE: attachment CONTENTS were NOT downloaded - only filenames are listed."
    Write-Host "      Re-run with -IncludeAttachmentText to extract+scrub docx/pdf/txt text."
}
Write-Host ""
Write-Host "Grade by pseudonym, write proposed-grades.json [ {id,score,comment} ], then run Post-Grades.ps1."
Write-Host "Audit: $auditPath"
