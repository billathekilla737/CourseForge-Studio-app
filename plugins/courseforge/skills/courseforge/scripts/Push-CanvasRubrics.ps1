<#
  Push-CanvasRubrics.ps1 (courseforge) - create REAL Canvas Rubric objects and
  attach them to assignments (or graded discussions' backing assignments), so
  they drive SpeedGrader and the gradebook - not just a styled HTML card.

  Manifest (JSON array; UTF-8):
  [
    {
      "assignment": "Final Submission",        // assignment NAME or numeric id
      "title": "Final project rubric",
      "use_for_grading": true,                  // rubric score becomes the grade
      "criteria": [
        { "description": "Gameplay & mechanics",
          "long_description": "Core loop works; no blocking bugs.",
          "points": 40,
          "ratings": [
            { "description": "Excellent", "points": 40 },
            { "description": "Adequate",  "points": 28 },
            { "description": "Missing",   "points": 0 }
          ] },
        ...
      ]
    }, ...
  ]

  Behavior:
    - Dry-run by default (prints what would be created/updated); -Apply to write.
    - Idempotent: an existing course rubric with the same TITLE is UPDATED
      (criteria replaced), not duplicated; the association is (re)pointed at the
      assignment.
    - Points sanity: each criterion's max rating must equal the criterion points;
      the rubric total is reported so you can eyeball it against the assignment.
    - Touches rubric DEFINITIONS only (course content). Rubric ASSESSMENTS
      (per-student scores) are student data - never read here, and the
      canvas-pii-guard denies those endpoints.

  Usage:
    .\Push-CanvasRubrics.ps1 -ManifestPath .\rubrics.json [-CourseId <id>] [-Apply]

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)] [string]$ManifestPath,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$tok   = (Get-Content $ctx.TokenPath -Raw).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$api   = "$base/api/v1/courses/$cid"
$hdr   = @{ Authorization = "Bearer $tok" }

$manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
$entries  = @($manifest)
if ($entries.Count -eq 0) { Write-Output 'Manifest is empty.'; exit 0 }

# form-encoded body from ordered key/value pairs (nested rubric params need
# exact bracket keys; hashtables would scramble order and PS 5.1 mis-encodes)
function Send-CanvasForm {
    param([string]$Method, [string]$Url, [System.Collections.ArrayList]$Pairs)
    $encoded = foreach ($p in $Pairs) {
        '{0}={1}' -f [uri]::EscapeDataString([string]$p[0]), [uri]::EscapeDataString([string]$p[1])
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($encoded -join '&'))
    return Invoke-RestMethod -Method $Method -Uri $Url -Headers $hdr -Body $bytes `
        -ContentType 'application/x-www-form-urlencoded; charset=utf-8'
}

$assignments = Get-CanvasPaged -Url "$api/assignments?per_page=100" -Headers $hdr
$rubrics     = Get-CanvasPaged -Url "$api/rubrics?per_page=100" -Headers $hdr

foreach ($e in $entries) {
    # resolve the assignment by id or exact name
    $target = $null
    if ("$($e.assignment)" -match '^\d+$') {
        $target = $assignments | Where-Object { "$($_.id)" -eq "$($e.assignment)" } | Select-Object -First 1
    } else {
        $target = $assignments | Where-Object { $_.name -eq $e.assignment } | Select-Object -First 1
    }
    if (-not $target) {
        Write-Output ("  SKIP '{0}': assignment '{1}' not found in course {2}" -f $e.title, $e.assignment, $cid)
        continue
    }

    # points sanity
    $total = 0
    $warn = @()
    foreach ($c in @($e.criteria)) {
        $total += [double]$c.points
        $maxRating = (@($c.ratings) | Measure-Object -Property points -Maximum).Maximum
        if ([double]$maxRating -ne [double]$c.points) {
            $warn += ("criterion '{0}': max rating {1} != criterion points {2}" -f $c.description, $maxRating, $c.points)
        }
    }
    $existing = $rubrics | Where-Object { $_.title -eq $e.title } | Select-Object -First 1
    $mode = if ($existing) { 'UPDATE' } else { 'CREATE' }
    Write-Output ("  {0} rubric '{1}' ({2} criteria, {3} pts) -> assignment '{4}' (id {5}){6}" -f
        $mode, $e.title, @($e.criteria).Count, $total, $target.name, $target.id,
        $(if ($e.use_for_grading) { ' [grades]' } else { '' }))
    foreach ($w in $warn) { Write-Output ("    WARN: {0}" -f $w) }
    if (-not $Apply) { continue }

    # build the nested form body
    $pairs = New-Object System.Collections.ArrayList
    [void]$pairs.Add(@('rubric[title]', $e.title))
    [void]$pairs.Add(@('rubric[free_form_criterion_comments]', '0'))
    $ci = 0
    foreach ($c in @($e.criteria)) {
        $ck = "rubric[criteria][$ci]"
        [void]$pairs.Add(@("$ck[description]", $c.description))
        if ($c.long_description) { [void]$pairs.Add(@("$ck[long_description]", $c.long_description)) }
        [void]$pairs.Add(@("$ck[points]", [string]$c.points))
        [void]$pairs.Add(@("$ck[criterion_use_range]", 'false'))
        $ri = 0
        foreach ($r in @($c.ratings)) {
            [void]$pairs.Add(@("$ck[ratings][$ri][description]", $r.description))
            [void]$pairs.Add(@("$ck[ratings][$ri][points]", [string]$r.points))
            $ri++
        }
        $ci++
    }
    [void]$pairs.Add(@('rubric_association[association_type]', 'Assignment'))
    [void]$pairs.Add(@('rubric_association[association_id]', [string]$target.id))
    [void]$pairs.Add(@('rubric_association[use_for_grading]', $(if ($e.use_for_grading) { '1' } else { '0' })))
    [void]$pairs.Add(@('rubric_association[purpose]', 'grading'))

    if ($existing) {
        $resp = Send-CanvasForm PUT "$api/rubrics/$($existing.id)" $pairs
    } else {
        $resp = Send-CanvasForm POST "$api/rubrics" $pairs
    }
    $rid = if ($resp.rubric) { $resp.rubric.id } else { $resp.id }
    Write-Output ("    ok rubric id {0} attached to assignment {1}" -f $rid, $target.id)
}

if (-not $Apply) { Write-Output ''; Write-Output 'Dry run. Re-run with -Apply to write.' }
