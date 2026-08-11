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
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\pdf-text-work',
    [string]$FileId,
    [string]$Pattern,        # regex for Fetch scan
    [switch]$Apply           # Push without -Apply = dry run
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$token = (Get-Content -Raw $ctx.TokenPath).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $token" }
$PDF_CT = 'application/pdf'
$py    = Join-Path $PSScriptRoot 'pdf_text_tool.py'

function Get-CoursePdfs {
    $url = "$base/api/v1/courses/$cid/files?per_page=100&content_types[]=$PDF_CT"
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
    if ($FileId) { $out = @($out | Where-Object { "$($_.id)" -eq "$FileId" }) }
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
            ConvertTo-Json | Set-Content -Path (Join-Path $d 'file.json') -Encoding ASCII
        Write-Output ("fetched {0} -> {1}" -f $f.display_name, $orig)
        & python $py scan $orig --pattern $Pattern --json (Join-Path $d 'scan.json')
    }
    Write-Output ""
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
    $orig  = Join-Path $dir.FullName 'original.pdf'
    $map   = Join-Path $dir.FullName 'map.json'
    $fixed = Join-Path $dir.FullName 'fixed.pdf'
    if (-not (Test-Path $map)) { Write-Output ("SKIP {0}: no map.json" -f $meta.display_name); continue }
    & python $py apply $orig --map $map --out $fixed --json (Join-Path $dir.FullName 'apply-report.json')
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
