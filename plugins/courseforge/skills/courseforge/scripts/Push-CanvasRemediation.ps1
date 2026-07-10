<#
  Push-CanvasRemediation.ps1 (courseforge) - step 3 of the existing-course
  remediation pipeline (Dump-CanvasContent.ps1 -> restyle_html.py -> THIS).

  Pushes restyled bodies back IN PLACE:
    Page       -> PUT /pages/:slug          wiki_page[body]
    Assignment -> PUT /assignments/:id      assignment[description]
    Discussion -> PUT /discussion_topics/:id message
    Quiz       -> PUT /quizzes/:id          quiz[description]
    Syllabus   -> PUT /courses/:id          course[syllabus_body]

  SAFETY (each learned the hard way):
    - NEVER touches modules, publish state, titles, or anything but the body field.
    - Dry-run by default; -Apply to write.
    - REFUSES to run unless restyle_html.py `verify` passed (verify-report.json,
      zero failures) - the proof the visible text is unchanged.
    - Empty-body guard: a missing/empty styled file is skipped with an error,
      never PUT (an empty PUT returns 200 and silently wipes the item).
    - Test-writes the FIRST item and stops on 403 (concluded/write-locked course)
      before touching anything else.
    - Styled bodies are pure-ASCII entities (restyle_html.py asciify), so the
      Canvas raw-emoji 500 cannot occur.
    - Live re-verify after -Apply: every item is fetched back and checked
      non-empty (+ navy fill present for hybrid/rich looks).

  Usage:
    .\Push-CanvasRemediation.ps1 -WorkDir .\remediation-work\721874           # dry run
    .\Push-CanvasRemediation.ps1 -WorkDir .\remediation-work\721874 -Apply    # write

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)] [string]$WorkDir,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string[]]$Kinds = @('Page','Assignment','Discussion','Quiz','Syllabus'),
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"

$manifestPath = Join-Path $WorkDir 'manifest.json'
if (-not (Test-Path $manifestPath)) { throw "No manifest.json in $WorkDir - run Dump-CanvasContent.ps1 first." }
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json

# resolve credentials; default the course to the manifest's course
if (-not $CourseId) { $CourseId = [string]$manifest.course_id }
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
if ([string]$cfg.course_id -ne [string]$manifest.course_id) {
    throw ("Config course ({0}) does not match manifest course ({1}). Wrong folder or wrong config." -f $cfg.course_id, $manifest.course_id)
}
$token = (Get-Content -Raw $ctx.TokenPath).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$api   = "$base/api/v1/courses/$cid"
$hdr   = @{ Authorization = "Bearer $token" }

# --- gate: verify must have passed -------------------------------------------
$verifyPath = Join-Path $WorkDir 'verify-report.json'
if (-not (Test-Path $verifyPath)) {
    throw "No verify-report.json - run: python restyle_html.py verify $WorkDir (must pass) before pushing."
}
$verify = Get-Content -Raw -Encoding UTF8 $verifyPath | ConvertFrom-Json
$failed = @($verify | Where-Object { -not $_.ok })
if ($failed.Count -gt 0) {
    throw ("verify-report.json has {0} failing item(s). Fix and re-verify before pushing." -f $failed.Count)
}

$look  = $manifest.look
$items = @($manifest.items | Where-Object { $_.styled_file -and ($Kinds -contains $_.kind) })
if ($items.Count -eq 0) { Write-Host 'Nothing to push (no styled items match).'; exit 0 }

function Get-FieldName([string]$kind) {
    switch ($kind) {
        'Page'       { 'wiki_page[body]' }
        'Assignment' { 'assignment[description]' }
        'Discussion' { 'message' }
        'Quiz'       { 'quiz[description]' }
        'Syllabus'   { 'course[syllabus_body]' }
    }
}
function Get-PutUrl($it) {
    switch ($it.kind) {
        'Page'       { "$api/pages/$($it.slug)" }
        'Assignment' { "$api/assignments/$($it.id)" }
        'Discussion' { "$api/discussion_topics/$($it.id)" }
        'Quiz'       { "$api/quizzes/$($it.id)" }
        'Syllabus'   { $api }
    }
}
function Get-BodyField($json, [string]$kind) {
    switch ($kind) {
        'Page'       { $json.body }
        'Assignment' { $json.description }
        'Discussion' { $json.message }
        'Quiz'       { $json.description }
        'Syllabus'   { $json.syllabus_body }
    }
}

Write-Host ("{0}: {1} item(s) -> course {2} ({3} look){4}" -f
    $(if ($Apply) { 'PUSH' } else { 'DRY RUN' }), $items.Count, $cid, $look,
    $(if ($Apply) { '' } else { '  (re-run with -Apply to write)' }))

$done = 0; $errs = 0; $first = $true
foreach ($it in $items) {
    $bodyText = ''
    if (Test-Path $it.styled_file) { $bodyText = [IO.File]::ReadAllText($it.styled_file) }
    if (-not $bodyText -or $bodyText.Trim().Length -lt 20) {
        Write-Host ("  ERROR  {0} '{1}': styled file empty/missing - SKIPPED (would wipe)" -f $it.kind, $it.name)
        $errs++; continue
    }
    if (-not $Apply) {
        Write-Host ("  would PUT  {0,-11} {1}  ({2} chars)" -f $it.kind, $it.name, $bodyText.Length)
        continue
    }
    try {
        if ($it.kind -eq 'Quiz') {
            # Classic Quizzes silently IGNORE form-encoded description updates
            # (HTTP 200, no change) - same family as the tabs API. Send JSON.
            $payload = @{ quiz = @{ description = $bodyText } } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Method Put -Uri (Get-PutUrl $it) -Headers $hdr `
                -ContentType 'application/json; charset=utf-8' `
                -Body ([Text.Encoding]::UTF8.GetBytes($payload)) | Out-Null
        } else {
            # Explicitly URL-encoded form body. Do NOT pass a hashtable to
            # Invoke-RestMethod here: PS 5.1's own form serializer mis-encodes
            # some bodies and Canvas then drops the param and 200-no-ops the PUT
            # (observed live: page PUT "succeeded", content unchanged).
            # EscapeDataString is chunked - it throws on very long strings.
            $sb = New-Object Text.StringBuilder
            [void]$sb.Append([uri]::EscapeDataString((Get-FieldName $it.kind))).Append('=')
            for ($i = 0; $i -lt $bodyText.Length; $i += 30000) {
                $len = [Math]::Min(30000, $bodyText.Length - $i)
                [void]$sb.Append([uri]::EscapeDataString($bodyText.Substring($i, $len)))
            }
            Invoke-RestMethod -Method Put -Uri (Get-PutUrl $it) -Headers $hdr `
                -ContentType 'application/x-www-form-urlencoded; charset=utf-8' `
                -Body $sb.ToString() | Out-Null
        }
        $done++
        Write-Host ("  ok   {0,-11} {1}" -f $it.kind, $it.name)
    } catch {
        $status = ''
        try { $status = [int]$_.Exception.Response.StatusCode } catch {}
        if ($first -and "$status" -eq '403') {
            throw ("FIRST write returned 403 on {0} '{1}': the course is likely write-locked " +
                   "(concluded / closed grading period). NOTHING has been changed. " +
                   "Have the instructor/registrar re-open it.") -f $it.kind, $it.name
        }
        Write-Host ("  FAIL({0}) {1,-11} {2}" -f $status, $it.kind, $it.name)
        $errs++
    }
    $first = $false
}

if (-not $Apply) { Write-Host ''; Write-Host 'Dry run complete. Nothing written.'; exit 0 }

# --- live re-verify -----------------------------------------------------------
Write-Host ''
Write-Host 'Live re-verify (fetching every pushed item back):'
$liveFails = 0
foreach ($it in $items) {
    try {
        $url = Get-PutUrl $it
        if ($it.kind -eq 'Syllabus') { $url = $api + '?include[]=syllabus_body' }
        $resp = Invoke-WebRequest -Uri $url -Headers $hdr -UseBasicParsing
        $json = ([Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())) | ConvertFrom-Json
        $live = [string](Get-BodyField $json $it.kind)
        $issues = @()
        if (-not $live -or $live.Trim().Length -lt 20) { $issues += 'EMPTY on live course' }
        # navy is only EXPECTED when this item actually gained a fill (wrapped hero,
        # or a templated body whose components matched). A templated body with no
        # hero/footer legitimately gains 0 fills under hybrid.
        $expectNavy = ($look -ne 'clean') -and ([int]$it.fills_added -gt 0)
        if ($expectNavy -and $live -notmatch '#061[eE]3[fF]') {
            $issues += 'navy fill missing'
        }
        if ($issues.Count -gt 0) { $liveFails++; Write-Host ("  FAIL {0,-11} {1}: {2}" -f $it.kind, $it.name, ($issues -join '; ')) }
        else                     { Write-Host ("  ok   {0,-11} {1} ({2} chars live)" -f $it.kind, $it.name, $live.Length) }
    } catch {
        $liveFails++; Write-Host ("  FAIL {0,-11} {1}: fetch-back error" -f $it.kind, $it.name)
    }
}

Write-Host ''
Write-Host ("PUSH COMPLETE: {0} written, {1} push error(s), {2} live-verify failure(s)." -f $done, $errs, $liveFails)
if (($errs + $liveFails) -gt 0) { exit 1 } else { exit 0 }
