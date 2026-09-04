<#
  Check-SLOAlignment.ps1 (courseforge) - cross-reference a Canvas shell against the
  Mississippi Student Learning Outcomes for its program, for ANY program (automotive,
  networking, cyber security, cloud, welding, business, health sciences, ...).

  Same two-phase gateway as the PPTX/DOCX/PDF remediators: a scripted Fetch collects
  facts, the AGENT does the judging, then a scripted Report validates and renders.

    -Action Resolve   course_code -> candidate MCCB program(s). Read-only, no downloads.
    -Action Fetch     resolve + download the framework PDF + extract THIS course's SLOs +
                      inventory every graded item in the Canvas course -> work folder.
    -Action Report    validate the agent's alignment.json, render markdown + Canvas-safe
                      HTML. -Apply additionally pushes the HTML as an UNPUBLISHED page.

  Between Fetch and Report the agent writes <work>\alignment.json:

    { "program": "...", "framework": "...pdf", "framework_year": 2025,
      "framework_url": "https://...",
      "alignment": [
        { "slo": "1.a",
          "verdict": "assessed|partially-assessed|ungraded-only|not-assessed|not-applicable",
          "evidence": [15977654, 5352405],        // Canvas item ids from items.json
          "rationale": "why this verdict",
          "suggestion": "what to add or change (required for a gap)" }, ... ] }

  The validator refuses a mapping that omits an outcome, invents an outcome id, cites an
  item id that is not in the course, or claims coverage with no evidence. Read the SLO verbs
  literally: an outcome that says CREATE / IMPLEMENT / COMPILE is not satisfied by an essay.

  Content-plane only: reads course settings, assignments, quizzes, discussions, pages and
  modules. Never reads submissions, grades or roster data.

  Usage:
    .\Check-SLOAlignment.ps1 -Action Resolve -CourseId 734391
    .\Check-SLOAlignment.ps1 -Action Fetch   -CourseId 734391 [-Slug /curriculum/...] [-Course "IMT 1213"]
    .\Check-SLOAlignment.ps1 -Action Report  -CourseId 734391 [-Apply]

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('Resolve','Fetch','Report')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$Slug,          # /curriculum/<program>; skip auto-resolution
    [string]$Course,        # state course code, e.g. "IMT 1213"; defaults to the Canvas course_code
    [string]$Title,         # course title, for title-based matching when numbers drift
    [string]$WorkDir,
    [switch]$UsePrior,      # use the prior-year framework instead of the current one
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx  = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg  = $ctx.Config
$tok  = $ctx.Token
$base = $cfg.base_url.TrimEnd('/')
$cid  = $cfg.course_id
$api  = "$base/api/v1/courses/$cid"
$hdr  = @{ Authorization = "Bearer $tok" }
$py   = Join-Path $PSScriptRoot 'slo_framework_tool.py'

if (-not $WorkDir) { $WorkDir = Join-Path (Split-Path $ctx.ConfigPath -Parent) "slo-alignment\$cid" }
$null = New-Item -ItemType Directory -Force -Path $WorkDir
$cacheFile = Join-Path $WorkDir 'programs.json'

function Get-Api {
    param([string]$Url, [switch]$Soft)
    try {
        return Invoke-RestMethod -Method GET -Uri $Url -Headers $hdr
    } catch {
        if ($Soft) {
            Write-Warning "GET $Url failed ($($_.Exception.Message)); skipping"
            return $null
        }
        throw "GET $Url failed: $($_.Exception.Message)"
    }
}
function Get-Paged {
    param([string]$Path)
    $out = New-Object System.Collections.ArrayList
    $page = 1
    while ($true) {
        # NOT -like '*?*': in a -like pattern '?' is a single-char wildcard, so that test is
        # true for every non-empty string and every URL got '&' instead of '?'.
        $sep = if ($Path.Contains('?')) { '&' } else { '?' }
        $url = "$api$Path$sep" + "per_page=100&page=$page"
        # Do NOT rely on PowerShell unrolling a function's array return: wrapping in @() can
        # yield a ONE-element array holding the whole response, after which $a.id silently
        # member-enumerates into an array of every id. Test for an array explicitly.
        $resp = Get-Api -Url $url
        $r = if ($null -eq $resp) { @() }
             elseif ($resp -is [System.Collections.IEnumerable] -and -not ($resp -is [string])) { @($resp) }
             else { @($resp) }
        if ($r.Count -eq 0) { break }
        foreach ($x in $r) { [void]$out.Add($x) }
        if ($r.Count -lt 100) { break }
        $page++
        if ($page -gt 20) { break }
    }
    # .ToArray() + @() at the call site: returning the ArrayList itself made the caller's
    # foreach see ONE object, and $a.id then member-enumerated into an array of all ids.
    return $out.ToArray()
}
function Strip-Html {
    param([string]$H)
    if (-not $H) { return '' }
    $t = [regex]::Replace($H, '<script.*?</script>', ' ', 'Singleline')
    $t = [regex]::Replace($t, '<[^>]+>', ' ')
    $t = [System.Net.WebUtility]::HtmlDecode($t)
    return ([regex]::Replace($t, '\s+', ' ')).Trim()
}

$canvasCourse = Get-Api -Url $api
$courseCode = [string]$canvasCourse.course_code
if (-not $courseCode) { $courseCode = [string]$canvasCourse.name }
Write-Output "Canvas course : $($canvasCourse.name)"
Write-Output "course_code   : $courseCode"

# ---------------------------------------------------------------- Resolve
if ($Action -eq 'Resolve') {
    & python $py index --cache $cacheFile | Write-Output
    & python $py resolve --course-code "$courseCode" --cache $cacheFile
    Write-Output ''
    Write-Output 'If more than one program shares the prefix, run Fetch on each candidate -Slug and'
    Write-Output 'match the course by NUMBER and TITLE; local numbers drift from state numbers.'
    exit 0
}

# ---------------------------------------------------------------- Fetch
if ($Action -eq 'Fetch') {
    if (-not $Slug) {
        $null = & python $py index --cache $cacheFile
        $json = & python $py resolve --course-code "$courseCode" --cache $cacheFile --json | Out-String
        $res  = $json | ConvertFrom-Json
        $cands = @($res.candidates)
        if ($cands.Count -eq 0) {
            Write-Output ''
            Write-Output "NO CTE FRAMEWORK: $($res.conclusion)"
            exit 3
        }
        if ($cands.Count -gt 1) {
            Write-Output ''
            Write-Output "AMBIGUOUS ($($cands.Count) programs share this prefix):"
            foreach ($c in $cands) { Write-Output ("  {0,-58} CIP {1,-9} {2}" -f $c.name, $c.cip, $c.slug) }
            Write-Output 'Re-run with -Slug <one of the above>.'
            exit 4
        }
        $Slug = $cands[0].slug
        Write-Output "Program       : $($cands[0].name) (CIP $($cands[0].cip))"
    }

    $fwDir = Join-Path $WorkDir 'framework'
    & python $py fetch --slug $Slug --out $fwDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $src = Get-Content (Join-Path $fwDir 'framework-sources.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $want = if ($UsePrior) { $src.pdfs | Where-Object { -not $_.current } } else { $src.pdfs | Where-Object { $_.current } }
    if (-not $want) { $want = $src.pdfs }
    $pdf = @($want)[0]
    Write-Output "Framework     : $($pdf.path) (year $($pdf.year))"

    $stateCourse = if ($Course) { $Course } else { ($courseCode -replace '^\s*([A-Za-z]{2,4})\s*(\d{3,4}).*$', '$1 $2') }
    $titleArg = if ($Title) { $Title } else { $canvasCourse.name }
    & python $py slos --pdf $pdf.path --course "$stateCourse" --title "$titleArg" --out (Join-Path $WorkDir 'slos.json')
    $sloExit = $LASTEXITCODE

    # ---- Canvas inventory: every item that could carry evidence
    Write-Output ''
    Write-Output 'Inventorying Canvas items...'
    $items = New-Object System.Collections.ArrayList
    $rawAssignments = Get-Paged -Path '/assignments'
    foreach ($a in $rawAssignments) {
        [void]$items.Add([pscustomobject]@{
            id = $a.id; type = 'Assignment'; name = $a.name; points = $a.points_possible
            graded = $true; published = $a.published
            submission_types = ($a.submission_types -join ','); due_at = $a.due_at
            text = (Strip-Html $a.description) })
    }
    $rawQuizzes = Get-Paged -Path '/quizzes'
    foreach ($q in $rawQuizzes) {
        [void]$items.Add([pscustomobject]@{
            id = $q.id; type = 'Quiz'; name = $q.title; points = $q.points_possible
            graded = ($q.quiz_type -eq 'assignment' -or $q.quiz_type -eq 'graded_survey')
            published = $q.published; submission_types = $q.quiz_type; due_at = $q.due_at
            question_count = $q.question_count
            text = (Strip-Html $q.description) })
    }
    $rawDiscussions = Get-Paged -Path '/discussion_topics'
    foreach ($d in $rawDiscussions) {
        [void]$items.Add([pscustomobject]@{
            id = $d.id; type = 'Discussion'; name = $d.title
            points = $(if ($d.assignment) { $d.assignment.points_possible } else { $null })
            graded = [bool]$d.assignment; published = $d.published
            submission_types = 'discussion'; due_at = $null
            text = (Strip-Html $d.message) })
    }
    $rawPages = Get-Paged -Path '/pages'
    foreach ($p in $rawPages) {
        # a page body needs its own GET; a single unreadable page must not sink the inventory
        $slugEnc = [uri]::EscapeDataString([string]$p.url)
        $full = Get-Api -Url "$api/pages/$slugEnc" -Soft
        [void]$items.Add([pscustomobject]@{
            id = $p.page_id; type = 'Page'; name = $p.title; points = $null
            graded = $false; published = $p.published; submission_types = 'n/a'; due_at = $null
            text = $(if ($full) { Strip-Html $full.body } else { '' }) })
    }

    $modules = @()
    $rawModules = Get-Paged -Path '/modules?include%5B%5D=items'
    foreach ($m in $rawModules) {
        $modules += [pscustomobject]@{ name = $m.name; position = $m.position
            items = @($m.items | ForEach-Object { [pscustomobject]@{ type = $_.type; title = $_.title; content_id = $_.content_id } }) }
    }

    $inv = [pscustomobject]@{
        course_id = $cid; course_name = $canvasCourse.name; course_code = $courseCode
        workflow_state = $canvasCourse.workflow_state
        program_slug = $Slug; framework = $pdf.path; framework_year = $pdf.year
        framework_url = $pdf.url; framework_page = $src.page
        counts = [pscustomobject]@{
            graded = @($items | Where-Object { $_.graded }).Count
            ungraded = @($items | Where-Object { -not $_.graded }).Count }
        items = $items; modules = $modules }
    $invPath = Join-Path $WorkDir 'items.json'
    $json = $inv | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($invPath, $json, (New-Object Text.UTF8Encoding($false)))

    Write-Output ("  graded items   : {0}" -f $inv.counts.graded)
    Write-Output ("  ungraded items : {0}" -f $inv.counts.ungraded)
    Write-Output ''
    Write-Output "work folder   : $WorkDir"
    Write-Output '  slos.json   - the state Student Learning Outcomes for this course'
    Write-Output '  items.json  - every Canvas item that could carry evidence'
    Write-Output ''
    if ($sloExit -ne 0) {
        Write-Output 'SLO extraction did NOT produce outcomes for this course (see message above).'
        Write-Output 'Resolve that before writing any alignment.'
        exit $sloExit
    }
    Write-Output 'NEXT (agent): read slos.json + items.json, then write alignment.json as described'
    Write-Output 'in this script header. Judge the SLO verb literally. Then run -Action Report.'
    exit 0
}

# ---------------------------------------------------------------- Report
if ($Action -eq 'Report') {
    $al = Join-Path $WorkDir 'alignment.json'
    foreach ($f in @($al, (Join-Path $WorkDir 'slos.json'), (Join-Path $WorkDir 'items.json'))) {
        if (-not (Test-Path $f)) { Write-Output "MISSING: $f"; exit 2 }
    }
    & python $py validate --alignment $al --slos (Join-Path $WorkDir 'slos.json') --items (Join-Path $WorkDir 'items.json')
    if ($LASTEXITCODE -ne 0) { Write-Output 'Fix alignment.json and re-run.'; exit 2 }

    $md   = Join-Path $WorkDir 'slo-alignment.md'
    $htmlF= Join-Path $WorkDir 'slo-alignment.html'
    & python $py report --alignment $al --slos (Join-Path $WorkDir 'slos.json') `
        --items (Join-Path $WorkDir 'items.json') --out-md $md --out-html $htmlF
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Output ''
    Write-Output "report        : $md"
    if (-not $Apply) {
        Write-Output 'DRY RUN. Nothing pushed to Canvas. Re-run with -Apply to add the report as an'
        Write-Output 'UNPUBLISHED Canvas page (instructor-facing; delete it before publishing the course).'
        exit 0
    }

    $body = Get-Content $htmlF -Raw -Encoding UTF8
    $pairs = @(
        @('wiki_page[title]', 'SLO Alignment Report (instructor only)'),
        @('wiki_page[body]',  $body),
        @('wiki_page[published]', 'false')
    )
    $encoded = foreach ($p in $pairs) { '{0}={1}' -f [uri]::EscapeDataString($p[0]), [uri]::EscapeDataString([string]$p[1]) }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($encoded -join '&'))
    $resp = Invoke-RestMethod -Method PUT -Uri "$api/pages/slo-alignment-report-instructor-only" `
        -Headers $hdr -Body $bytes -ContentType 'application/x-www-form-urlencoded; charset=utf-8'
    Write-Output "pushed page   : $($resp.html_url) (published=$($resp.published))"
    exit 0
}
