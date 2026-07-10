<#
  Dump-CanvasContent.ps1 (courseforge) - step 1 of the existing-course remediation
  pipeline (Dump -> restyle_html.py -> Push-CanvasRemediation.ps1).

  Downloads every HTML body Ally scans - pages, assignment descriptions, discussion
  topic messages, quiz descriptions, and the syllabus - into a local work folder,
  plus a manifest.json the rest of the pipeline consumes.

  Reads CONTENT only: page/assignment/quiz/discussion-topic bodies authored by the
  instructor. It never requests submissions, entries, or roster data (and the
  canvas-pii-guard block would deny those endpoints anyway).

  Auto-injected account theme assets (the instructure-uploads <link>/<script> pair
  Canvas prepends/appends ON READ) are stripped on save - they are not stored
  content, and pushing them back would double-inject them.

  Layout written:
    <WorkDir>\manifest.json
    <WorkDir>\bodies\<Kind>_<id>.html        (original, theme-stripped, UTF-8)

  Usage:
    .\Dump-CanvasContent.ps1                          # config resolved via CanvasContext
    .\Dump-CanvasContent.ps1 -CourseId 721874         # disambiguate multiple configs
    .\Dump-CanvasContent.ps1 -WorkDir .\rework\721874

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = ''
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$token = (Get-Content -Raw $ctx.TokenPath).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$api   = "$base/api/v1/courses/$cid"
$hdr   = @{ Authorization = "Bearer $token" }

if (-not $WorkDir) { $WorkDir = Join-Path (Get-Location) ("remediation-work\" + $cid) }
$bodiesDir = Join-Path $WorkDir 'bodies'
New-Item -ItemType Directory -Force -Path $bodiesDir | Out-Null

function Get-Paged([string]$url) {
    $out = @()
    while ($url) {
        $resp = Invoke-WebRequest -Uri $url -Headers $hdr -UseBasicParsing
        $out += @(([Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())) | ConvertFrom-Json)
        $url = $null
        if ($resp.Headers.Link) {
            foreach ($part in ($resp.Headers.Link -split ',')) {
                if ($part -match '<([^>]+)>;\s*rel="next"') { $url = $Matches[1] }
            }
        }
    }
    return $out
}

function Get-One([string]$url) {
    $resp = Invoke-WebRequest -Uri $url -Headers $hdr -UseBasicParsing
    return ([Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())) | ConvertFrom-Json
}

function Strip-Theme([string]$html) {
    if (-not $html) { return '' }
    $html = [regex]::Replace($html, '<link\b[^>]*instructure-uploads[^>]*>', '', 'IgnoreCase')
    $html = [regex]::Replace($html, '<script\b[^>]*instructure-uploads[^>]*>\s*</script>', '', 'IgnoreCase')
    return $html.Trim()
}

function Save-Body([string]$kind, $id, [string]$html) {
    $file = Join-Path $bodiesDir ("{0}_{1}.html" -f $kind, $id)
    [IO.File]::WriteAllText($file, $html, (New-Object Text.UTF8Encoding($false)))
    return $file
}

$entries = @()

Write-Host "Dumping course $cid content -> $WorkDir"

# --- Pages (list gives slugs; body needs a per-page fetch) -------------------
foreach ($p in (Get-Paged "$api/pages?per_page=100")) {
    $full = Get-One "$api/pages/$($p.page_id)"
    $body = Strip-Theme $full.body
    $file = Save-Body 'Page' $p.page_id $body
    $entries += [ordered]@{ kind='Page'; id=$p.page_id; name=$p.title; slug=$p.url
                            published=[bool]$p.published; chars=$body.Length; file=$file }
    Write-Host ("  Page        {0}  ({1} chars)" -f $p.title, $body.Length)
}

# --- Assignments (descriptions come with the list) ---------------------------
# Quiz-backed and discussion-backed assignments are SHELLS: Canvas 400s a PUT on
# assignment[description] for them, and their real body is the quiz description /
# discussion message we dump separately. Skip the shells here.
foreach ($a in (Get-Paged "$api/assignments?per_page=100")) {
    $types = @($a.submission_types)
    if ($a.quiz_id -or ($types -contains 'online_quiz') -or ($types -contains 'discussion_topic')) {
        Write-Host ("  Assignment  {0}  (skipped: quiz/discussion-backed shell)" -f $a.name)
        continue
    }
    $body = Strip-Theme $a.description
    $file = Save-Body 'Assignment' $a.id $body
    $entries += [ordered]@{ kind='Assignment'; id=$a.id; name=$a.name; slug=$null
                            published=[bool]$a.published; chars=$body.Length; file=$file }
    Write-Host ("  Assignment  {0}  ({1} chars)" -f $a.name, $body.Length)
}

# --- Discussion topics (instructor message only; never entries) --------------
foreach ($d in (Get-Paged "$api/discussion_topics?per_page=100")) {
    $body = Strip-Theme $d.message
    $file = Save-Body 'Discussion' $d.id $body
    $entries += [ordered]@{ kind='Discussion'; id=$d.id; name=$d.title; slug=$null
                            published=[bool]$d.published; chars=$body.Length; file=$file }
    Write-Host ("  Discussion  {0}  ({1} chars)" -f $d.title, $body.Length)
}

# --- Quiz descriptions --------------------------------------------------------
foreach ($q in (Get-Paged "$api/quizzes?per_page=100")) {
    $body = Strip-Theme $q.description
    $file = Save-Body 'Quiz' $q.id $body
    $entries += [ordered]@{ kind='Quiz'; id=$q.id; name=$q.title; slug=$null
                            published=[bool]$q.published; chars=$body.Length; file=$file }
    Write-Host ("  Quiz        {0}  ({1} chars)" -f $q.title, $body.Length)
}

# --- Syllabus -----------------------------------------------------------------
$course = Get-One ($api + '?include[]=syllabus_body')
if ($course.syllabus_body -and $course.syllabus_body.Trim()) {
    $body = Strip-Theme $course.syllabus_body
    $file = Save-Body 'Syllabus' $cid $body
    $entries += [ordered]@{ kind='Syllabus'; id=$cid; name='Course Syllabus'; slug=$null
                            published=$true; chars=$body.Length; file=$file }
    Write-Host ("  Syllabus    ({0} chars)" -f $body.Length)
}

$manifest = [ordered]@{
    course_id    = $cid
    base_url     = $base
    course_label = $cfg.course_label
    dumped_at    = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
    items        = $entries
}
$manifestPath = Join-Path $WorkDir 'manifest.json'
# BOM-less UTF-8: PS 5.1's Set-Content -Encoding UTF8 writes a BOM, which breaks
# python json.load downstream.
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Write-Host ("Dumped {0} items. Manifest: {1}" -f $entries.Count, $manifestPath)
Write-Host "NEXT: python restyle_html.py transform <WorkDir> --look hybrid   (then verify, then Push-CanvasRemediation.ps1)"
