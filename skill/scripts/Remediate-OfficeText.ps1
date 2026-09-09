<#
  Remediate-OfficeText.ps1 (courseforge) - scan and edit TEXT inside a course's
  Office files (.docx / .pptx / .xlsx), same three-phase gateway as the other
  remediators. This is the productized form of the change-request job: replace
  a former instructor's name/email/room across every document, fix a stale
  date, correct Author metadata - places the alt-text remediators never look.

    1) -Action List   : enumerate the course's OOXML files.
    2) -Action Fetch  : download each (original kept as original.<ext>) and run
                        office_text_tool.py scan with -Pattern. Legacy binary
                        .doc/.ppt/.xls are REPORTED as unsupported, never
                        silently skipped. Read-only.
    3) (agent phase)  : review scan.json, write <WorkDir>\<fileId>\map.json.
    4) -Action Push   : apply -> fixed.<ext> (verified: zero residual find
                        strings + zip integrity, refuses upload otherwise),
                        then upload over the SAME filename + folder with
                        on_duplicate=overwrite so links keep working.
                        DRY-RUN by default; -Apply to upload.

  Matching is raw-XML literal (see office_text_tool.py header for the split-run
  caveat). Course FILES are course content; canvas-pii-guard still gates all
  endpoints. Requires Python 3 stdlib only.

  Usage:
    .\Remediate-OfficeText.ps1 -Action List
    .\Remediate-OfficeText.ps1 -Action Fetch -Pattern "old name|old@email"
    .\Remediate-OfficeText.ps1 -Action Push                    # dry run
    .\Remediate-OfficeText.ps1 -Action Push -Apply
    (-FileId <id> limits any action to one file; -SetAuthor/-SetLastModifiedBy
     rewrite core.xml properties on Push)

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\office-text-work',
    [string]$FileId,
    [string]$Pattern,           # regex for Fetch scan
    [string]$SetAuthor,         # Push: rewrite dc:creator in core.xml
    [string]$SetLastModifiedBy, # Push: rewrite cp:lastModifiedBy in core.xml
    [switch]$Apply              # Push without -Apply = dry run
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Script-scope error handler. With $ErrorActionPreference = 'Stop' and no
# handler, any Canvas hiccup printed a raw PowerShell stack trace at an
# instructional designer. Report it in one readable line (plus the 401/403/404
# meaning, which is what people actually need) and exit non-zero so callers
# can still tell this failed.
trap {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    Write-Host ''
    Write-Host ("{0} stopped: {1}" -f $MyInvocation.MyCommand.Name, $_.Exception.Message) -ForegroundColor Red
    switch ($code) {
        401 { Write-Host '  Canvas said 401 Unauthorized - the token is wrong, expired or revoked. Re-run Setup-Canvas.ps1.' -ForegroundColor Yellow }
        403 { Write-Host '  Canvas said 403 Forbidden - either no permission on this course, or a rate limit. Wait a minute and retry; if it repeats, check the course is not concluded.' -ForegroundColor Yellow }
        404 { Write-Host '  Canvas said 404 Not Found - check the course id in the config, and that the item still exists.' -ForegroundColor Yellow }
        default {
            if ($code -ge 500) { Write-Host '  Canvas returned a server error. This is usually transient - retry shortly.' -ForegroundColor Yellow }
        }
    }
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host ("  (line {0})" -f $_.InvocationInfo.ScriptLineNumber) -ForegroundColor DarkGray
    }
    Write-Host '  Nothing further was written by this run.' -ForegroundColor Yellow
    exit 1
}


. "$PSScriptRoot\CanvasContext.ps1"
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$token = $ctx.Token
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $token" }
$py    = Join-Path $PSScriptRoot 'office_text_tool.py'

# extension -> content type for the upload slot
$CT = @{
    '.docx' = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    '.pptx' = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    '.xlsx' = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
}

function Get-CourseOfficeFiles {
    # Fetch UNFILTERED; match extension OR content_type client-side (a server
    # content_types[] filter silently drops files with an empty content_type).
    # Legacy .doc/.ppt/.xls are returned too, tagged, so Fetch can REPORT them
    # as unsupported instead of pretending they do not exist.
    $url = "$base/api/v1/courses/$cid/files?per_page=100"
    $out = @()
    while ($url) {
        $resp = Invoke-WebRequest -Uri $url -Headers $hdr -UseBasicParsing
        $out += ($resp.Content | ConvertFrom-Json)
        $url = $null
        if ($resp.Headers.Link) {
            foreach ($part in ($resp.Headers.Link -split ',')) {
                if ($part -match '<([^>]+)>;\s*rel="next"') { $url = $Matches[1] }
            }
        }
    }
    $out = @($out | Where-Object { $_.display_name -match '(?i)\.(docx|pptx|xlsx|doc|ppt|xls)$' })
    foreach ($f in $out) {
        $f | Add-Member -NotePropertyName legacy -NotePropertyValue ($f.display_name -match '(?i)\.(doc|ppt|xls)$') -Force
    }
    if ($FileId) { $out = @($out | Where-Object { "$($_.id)" -eq "$FileId" }) }
    if ($out.Count -eq 0) {
        Write-Warning ("No Office files matched{0} in course {1}." -f $(if ($FileId) { " -FileId $FileId" } else { '' }), $cid)
    }
    return $out
}

if ($Action -eq 'List') {
    foreach ($f in Get-CourseOfficeFiles) {
        $tag = if ($f.legacy) { '  [LEGACY - convert to OOXML first]' } else { '' }
        Write-Output ("{0}  {1}  ({2} KB, folder {3}){4}" -f
            $f.id, $f.display_name, [math]::Round($f.size/1KB), $f.folder_id, $tag)
    }
    exit 0
}

if ($Action -eq 'Fetch') {
    if (-not $Pattern) { throw "Fetch needs -Pattern (regex to scan for)." }
    $legacySkipped = 0
    foreach ($f in Get-CourseOfficeFiles) {
        if ($f.legacy) {
            Write-Output ("UNSUPPORTED {0} - legacy binary Office format; convert to OOXML first." -f $f.display_name)
            $legacySkipped++
            continue
        }
        $ext = [IO.Path]::GetExtension($f.display_name).ToLower()
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d ("original" + $ext)
        Invoke-WebRequest -Uri $f.url -OutFile $orig -UseBasicParsing
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id; ext = $ext } |
            ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $d 'file.json') -Encoding UTF8
        Write-Output ("fetched {0} -> {1}" -f $f.display_name, $orig)
        & python $py scan $orig --pattern $Pattern --json (Join-Path $d 'scan.json')
    }
    Write-Output ""
    if ($legacySkipped) { Write-Output ("{0} legacy file(s) NOT scanned - convert those by hand." -f $legacySkipped) }
    Write-Output "NEXT: review each <WorkDir>\<fileId>\scan.json, write map.json beside it, then -Action Push."
    exit 0
}

# ---- Push -------------------------------------------------------------------
$dirs = Get-ChildItem -Path $WorkDir -Directory -ErrorAction SilentlyContinue
if (-not $dirs) { throw "Nothing under $WorkDir - run -Action Fetch first." }
$failures = 0
foreach ($dir in $dirs) {
    if ($FileId -and $dir.Name -ne "$FileId") { continue }
    $meta  = Get-Content -Raw (Join-Path $dir.FullName 'file.json') | ConvertFrom-Json
    $orig  = Join-Path $dir.FullName ("original" + $meta.ext)
    $map   = Join-Path $dir.FullName 'map.json'
    $fixed = Join-Path $dir.FullName ("fixed" + $meta.ext)
    if (-not (Test-Path $map)) { Write-Output ("SKIP {0}: no map.json" -f $meta.display_name); continue }
    $pyArgs = @('apply', $orig, '--map', $map, '--out', $fixed, '--json', (Join-Path $dir.FullName 'apply-report.json'))
    if ($PSBoundParameters.ContainsKey('SetAuthor'))         { $pyArgs += @('--set-author', $SetAuthor) }
    if ($PSBoundParameters.ContainsKey('SetLastModifiedBy')) { $pyArgs += @('--set-lastmodifiedby', $SetLastModifiedBy) }
    & python $py @pyArgs
    if ($LASTEXITCODE -ne 0) { Write-Output ("VERIFY FAILED {0} - not uploading" -f $meta.display_name); $failures++; continue }
    if (-not $Apply) { Write-Output ("DRY RUN: would upload {0} over file id {1}" -f $fixed, $meta.id); continue }
    $size = (Get-Item $fixed).Length
    $ct = $CT[$meta.ext]; if (-not $ct) { $ct = 'application/octet-stream' }
    $slotBody = @{ name = $meta.display_name; size = $size; content_type = $ct;
                   parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
    $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
    $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
    foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('--form-string', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
    $curlArgs += @('-F', ('file=@{0}' -f $fixed))
    $code = & curl.exe @curlArgs
    if ("$code" -notmatch '^(200|201|3..)$') { Write-Output ("UPLOAD FAILED ({0}) {1}" -f $code, $meta.display_name); $failures++; continue }
    Write-Output ("UPLOADED {0} (original kept at {1})" -f $meta.display_name, $orig)
}
exit $(if ($failures) { 1 } else { 0 })
