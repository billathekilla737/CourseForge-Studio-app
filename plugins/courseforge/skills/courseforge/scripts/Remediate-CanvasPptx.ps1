<#
  Remediate-CanvasPptx.ps1 (courseforge) - Canvas legwork for PPTX ADA remediation.

  Pairs with remediate_pptx.py (same folder). The agent orchestrates the phases:

    1) -Action List   : enumerate .pptx files in the course (id, name, size, folder)
    2) -Action Fetch   : download every .pptx (or one via -FileId) into WorkDir\<fileId>\
                         keeping the ORIGINAL as original.pptx (the backup), then run
                         the python scanner so images + report.json are ready.
    3) (agent phase)   : the agent READS each extracted image, writes fixes.json
                         (alt text, decorative flags, slide titles) per deck.
    4) -Action Push    : run python apply -> fixed.pptx, then upload it back to the
                         SAME folder with on_duplicate=overwrite (same filename, so
                         every course link to the file keeps working).

  Course FILES are course content (not student data). This script never touches
  submission attachments; the canvas-pii-guard block hook still gates all endpoints.

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$WorkDir = '.\pptx-work',
    [string]$FileId,
    [switch]$Apply   # Push without -Apply = dry run (report what would upload)
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Resolve-Default([string]$path, [string]$pattern) {
    if ($path) { return $path }
    $here = Get-ChildItem -Path . -Filter $pattern -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($here) { return $here.FullName }
    $std = Join-Path $env:USERPROFILE 'Documents\canvas-work'
    $c = Get-ChildItem -Path $std -Filter $pattern -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.FullName }
    throw "Cannot find $pattern (looked in . and $std). Pass the path explicitly."
}

$ConfigPath = Resolve-Default $ConfigPath 'canvas.config.*.json'
$TokenPath  = Resolve-Default $TokenPath  'canvas.token'
$cfg   = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$tok   = (Get-Content $TokenPath -Raw).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $tok" }
$PPTX_CT = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
$py    = Join-Path $PSScriptRoot 'remediate_pptx.py'

function Get-CoursePptx {
    $url = "$base/api/v1/courses/$cid/files?per_page=100&content_types[]=$PPTX_CT"
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
    return $out
}

if ($Action -eq 'List') {
    $files = Get-CoursePptx
    Write-Output ("Found {0} .pptx file(s) in course {1}:" -f $files.Count, $cid)
    foreach ($f in $files) {
        Write-Output ("  id={0}  {1}  ({2} KB)  folder_id={3}" -f $f.id, $f.display_name, [math]::Round($f.size/1KB), $f.folder_id)
    }
    exit 0
}

if ($Action -eq 'Fetch') {
    $files = Get-CoursePptx
    if ($FileId) { $files = @($files | Where-Object { "$($_.id)" -eq "$FileId" }) }
    if (-not $files) { Write-Output 'No matching .pptx files.'; exit 0 }
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    foreach ($f in $files) {
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d 'original.pptx'
        Invoke-WebRequest -Uri $f.url -OutFile $orig -UseBasicParsing
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id } |
            ConvertTo-Json | Set-Content -Path (Join-Path $d 'file.json') -Encoding ASCII
        Write-Output ("fetched {0} -> {1}" -f $f.display_name, $orig)
        & python $py scan $orig --workdir (Join-Path $d 'work')
    }
    Write-Output ''
    Write-Output 'NEXT (agent): for each deck read work\report.json, VIEW each image under'
    Write-Output 'work\images\, write work\fixes.json, then run -Action Push.'
    exit 0
}

if ($Action -eq 'Push') {
    $dirs = Get-ChildItem -Path $WorkDir -Directory -ErrorAction Stop
    if ($FileId) { $dirs = @($dirs | Where-Object { $_.Name -eq "$FileId" }) }
    foreach ($d in $dirs) {
        $meta  = Get-Content (Join-Path $d.FullName 'file.json') -Raw | ConvertFrom-Json
        $orig  = Join-Path $d.FullName 'original.pptx'
        $work  = Join-Path $d.FullName 'work'
        $fixes = Join-Path $work 'fixes.json'
        $fixed = Join-Path $d.FullName 'fixed.pptx'
        if (-not (Test-Path $fixes)) { Write-Output ("SKIP {0}: no fixes.json yet" -f $meta.display_name); continue }
        & python $py apply $orig --workdir $work --out $fixed
        if ($LASTEXITCODE -ne 0) { throw "apply failed for $($meta.display_name)" }
        & python $py verify $fixed
        if (-not $Apply) { Write-Output ("DRY RUN: would upload {0} over file id {1}" -f $fixed, $meta.id); continue }
        # 3-step Canvas upload, same name + folder, overwrite (links keep working)
        $size = (Get-Item $fixed).Length
        $slotBody = @{ name = $meta.display_name; size = $size; content_type = $PPTX_CT;
                       parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
        $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
        # multipart via curl.exe (works on PS 5.1; Invoke-WebRequest -Form needs PS 6+)
        $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
        foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('-F', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
        $curlArgs += @('-F', ('file=@{0}' -f $fixed))
        $code = & curl.exe @curlArgs
        if ("$code" -notmatch '^(200|201|3..)$') { throw "upload failed ($code) for $($meta.display_name)" }
        Write-Output ("UPLOADED remediated {0} (original kept at {1})" -f $meta.display_name, $orig)
    }
    exit 0
}
