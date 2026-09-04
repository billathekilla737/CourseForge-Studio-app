<#
  Remediate-CanvasPdfText.ps1 (courseforge) - scan and fix PDF TEXT across a
  Canvas course, same three-phase gateway as the PPTX/DOCX remediation:

    1) -Action List   : enumerate the course's PDFs (id, name, folder, size).
    2) -Action Fetch  : download each PDF (original kept as original.pdf) and
                        run pdf_text_tool.py scan with -Pattern, writing
                        scan.json per file. Read-only.
    3) (agent phase)  : review the hits, write <WorkDir>\<fileId>\map.json
                        ([{ "find": "...", "replace": "..." }, ...]).
    4) -Action Push   : pdf_text_tool.py apply -> fixed.pdf (verified clean of
                        every "find" string, exit 2 otherwise), then upload
                        over the SAME filename + folder with
                        on_duplicate=overwrite so links keep working.
                        DRY-RUN by default; -Apply to upload.

  Scope: short factual strings (names, emails, phones, room numbers) drawn in
  a base font. Not a re-typesetter, not a PDF/UA tagger - see pdf_text_tool.py
  header. Course FILES are course content; canvas-pii-guard still gates all
  endpoints. Requires: pip install PyMuPDF.

  Usage:
    .\Remediate-CanvasPdfText.ps1 -Action List
    .\Remediate-CanvasPdfText.ps1 -Action Fetch -Pattern "old name|old@email"
    .\Remediate-CanvasPdfText.ps1 -Action Push            # dry run
    .\Remediate-CanvasPdfText.ps1 -Action Push -Apply
    (-FileId <id> limits any action to one file)

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Fill','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\pdf-text-work',
    [string]$FileId,
    [string]$Pattern,        # regex for Fetch scan
    [string]$SetAuthor,      # Push: overwrite the PDF /Author metadata field
    [string]$SetTitle,       # Push: overwrite the PDF /Title metadata field
    [switch]$UpdateToc,      # Push: rewrite outline/TOC entries with the same mappings
    [switch]$StripSignature, # remove signature fields -> honest UNSIGNED output
    [switch]$AllowSigned,    # keep a signature that will read as ALTERED (rarely right)
    [switch]$Apply           # Push without -Apply = dry run
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
$PDF_CT = 'application/pdf'
$py    = Join-Path $PSScriptRoot 'pdf_text_tool.py'

function Get-CoursePdfs {
    # Match on EXTENSION or content_type, and never on content_type alone: a file
    # uploaded through some paths lands with content_type empty/octet-stream, and a
    # content_types[] server filter silently drops it (Fetch then does nothing).
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
    $out = @($out | Where-Object { $_.display_name -match '(?i)\.pdf$' -or $_.content_type -eq $PDF_CT })
    if ($FileId) { $out = @($out | Where-Object { "$($_.id)" -eq "$FileId" }) }
    if (-not $out -or $out.Count -eq 0) {
        Write-Warning ("No PDFs matched{0} in course {1}." -f $(if ($FileId) { " -FileId $FileId" } else { '' }), $cid)
    }
    return $out
}

if ($Action -eq 'List') {
    foreach ($f in Get-CoursePdfs) {
        Write-Output ("{0}  {1}  ({2} KB, folder {3}, hidden={4})" -f
            $f.id, $f.display_name, [math]::Round($f.size/1KB), $f.folder_id, $f.hidden)
    }
    exit 0
}

if ($Action -eq 'Fetch') {
    if (-not $Pattern) { throw "Fetch needs -Pattern (regex to scan for)." }
    foreach ($f in Get-CoursePdfs) {
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d 'original.pdf'
        Invoke-WebRequest -Uri $f.url -OutFile $orig -UseBasicParsing
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id } |
            ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $d 'file.json') -Encoding ASCII
        Write-Output ("fetched {0} -> {1}" -f $f.display_name, $orig)
        & python $py scan $orig --pattern $Pattern --json (Join-Path $d 'scan.json')
    }
    Write-Output ""
    Write-Output "NEXT: review each <WorkDir>\<fileId>\scan.json, write map.json beside it, then -Action Push."
    exit 0
}

# ---- Fill (local only; writes filled.pdf, which Push then treats as input) ---
if ($Action -eq 'Fill') {
    $dirs = Get-ChildItem -Path $WorkDir -Directory -ErrorAction SilentlyContinue
    if (-not $dirs) { throw "Nothing under $WorkDir - run -Action Fetch first." }
    foreach ($dir in $dirs) {
        if ($FileId -and $dir.Name -ne "$FileId") { continue }
        $meta = Get-Content -Raw (Join-Path $dir.FullName 'file.json') | ConvertFrom-Json
        $fillMap = Join-Path $dir.FullName 'fill.json'
        if (-not (Test-Path $fillMap)) { Write-Output ("SKIP {0}: no fill.json" -f $meta.display_name); continue }
        $src = Join-Path $dir.FullName 'original.pdf'
        $out = Join-Path $dir.FullName 'filled.pdf'
        $fargs = @('fill', $src, '--map', $fillMap, '--out', $out, '--json', (Join-Path $dir.FullName 'fill-report.json'))
        if ($StripSignature) { $fargs += '--strip-signature' }
        if ($AllowSigned)    { $fargs += '--allow-signed' }
        & python $py @fargs
        if ($LASTEXITCODE -ne 0) { Write-Output ("FILL FAILED {0}" -f $meta.display_name) }
        else { Write-Output ("FILLED {0} -> {1}" -f $meta.display_name, $out) }
    }
    Write-Output ""
    Write-Output "NEXT: RENDER the filled page(s) to PNG and LOOK at them, then -Action Push (add map.json first if you also need replacements)."
    exit 0
}

# ---- Push -------------------------------------------------------------------
$dirs = Get-ChildItem -Path $WorkDir -Directory -ErrorAction SilentlyContinue
if (-not $dirs) { throw "Nothing under $WorkDir - run -Action Fetch first." }
$failures = 0
foreach ($dir in $dirs) {
    if ($FileId -and $dir.Name -ne "$FileId") { continue }
    $meta  = Get-Content -Raw (Join-Path $dir.FullName 'file.json') | ConvertFrom-Json
    $orig  = Join-Path $dir.FullName 'original.pdf'
    $map   = Join-Path $dir.FullName 'map.json'
    $fixed = Join-Path $dir.FullName 'fixed.pdf'
    if (-not (Test-Path $map)) { Write-Output ("SKIP {0}: no map.json" -f $meta.display_name); continue }
    $pyArgs = @('apply', $orig, '--map', $map, '--out', $fixed, '--json', (Join-Path $dir.FullName 'apply-report.json'))
    if ($PSBoundParameters.ContainsKey('SetAuthor')) { $pyArgs += @('--set-author', $SetAuthor) }
    if ($PSBoundParameters.ContainsKey('SetTitle'))  { $pyArgs += @('--set-title',  $SetTitle) }
    if ($UpdateToc)       { $pyArgs += '--update-toc' }
    if ($StripSignature)  { $pyArgs += '--strip-signature' }
    if ($AllowSigned)     { $pyArgs += '--allow-signed' }
    # Fill output, when present, is the input Push should edit further
    $filled = Join-Path $dir.FullName 'filled.pdf'
    if (Test-Path $filled) { $pyArgs[1] = $filled }
    & python $py @pyArgs
    if ($LASTEXITCODE -ne 0) { Write-Output ("VERIFY FAILED {0} - not uploading" -f $meta.display_name); $failures++; continue }
    if (-not $Apply) { Write-Output ("DRY RUN: would upload {0} over file id {1}" -f $fixed, $meta.id); continue }
    $size = (Get-Item $fixed).Length
    $slotBody = @{ name = $meta.display_name; size = $size; content_type = $PDF_CT;
                   parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
    $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
    $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
    foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('-F', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
    $curlArgs += @('-F', ('file=@{0}' -f $fixed))
    $code = & curl.exe @curlArgs
    if ("$code" -notmatch '^(200|201|3..)$') { Write-Output ("UPLOAD FAILED ({0}) {1}" -f $code, $meta.display_name); $failures++; continue }
    Write-Output ("UPLOADED {0} (original kept at {1})" -f $meta.display_name, $orig)
}
exit $(if ($failures) { 1 } else { 0 })
