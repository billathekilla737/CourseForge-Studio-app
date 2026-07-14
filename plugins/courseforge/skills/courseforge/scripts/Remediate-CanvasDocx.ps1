<#
  Remediate-CanvasDocx.ps1 (courseforge) - Canvas legwork for Word (.docx) ADA
  remediation. Mirrors Remediate-CanvasPptx.ps1; pairs with remediate_docx.py.

    1) -Action List   : enumerate .docx files in the course
    2) -Action Fetch  : download each (original.docx kept as backup) + scan
                        (extracts images + report.json with faux-heading candidates)
    3) (agent phase)  : agent VIEWS work\images\*, writes work\fixes.json - alt
                        text per image key, optional paragraph->Heading promotions
                        (opt-in; they change appearance), table_headers true
    4) -Action Push   : apply -> fixed.docx, re-verify, upload over the original
                        (same name + folder, on_duplicate=overwrite - links keep
                        working). Dry-run default; -Apply to upload.

  HONEST SCOPE: alt text + table header rows + opt-in heading promotion; not full
  document tagging. Course FILES only - never submission attachments.

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\docx-work',
    [string]$FileId,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$ConfigPath = $ctx.ConfigPath; $TokenPath = $ctx.TokenPath
$cfg   = $ctx.Config
$tok   = (Get-Content $TokenPath -Raw).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $tok" }
$DOCX_CT = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
$py    = Join-Path $PSScriptRoot 'remediate_docx.py'

function Get-CourseDocx {
    $url = "$base/api/v1/courses/$cid/files?per_page=100&content_types[]=$DOCX_CT"
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
    return @($out)
}

if ($Action -eq 'List') {
    $files = @(Get-CourseDocx)
    Write-Output ("Found {0} .docx file(s) in course {1}:" -f $files.Count, $cid)
    foreach ($f in $files) {
        Write-Output ("  id={0}  {1}  ({2} KB)  folder_id={3}" -f $f.id, $f.display_name, [math]::Round($f.size/1KB), $f.folder_id)
    }
    exit 0
}

if ($Action -eq 'Fetch') {
    $files = @(Get-CourseDocx)
    if ($FileId) { $files = @($files | Where-Object { "$($_.id)" -eq "$FileId" }) }
    if (-not $files) { Write-Output 'No matching .docx files.'; exit 0 }
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    foreach ($f in $files) {
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d 'original.docx'
        Invoke-WebRequest -Uri $f.url -OutFile $orig -UseBasicParsing
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id } |
            ConvertTo-Json | Set-Content -Path (Join-Path $d 'file.json') -Encoding ASCII
        Write-Output ("fetched {0} -> {1}" -f $f.display_name, $orig)
        & python $py scan $orig --workdir (Join-Path $d 'work')
    }
    Write-Output ''
    Write-Output 'NEXT (agent): read each work\report.json, VIEW images under work\images\,'
    Write-Output 'write work\fixes.json (alts; optional headings promotions), then -Action Push.'
    exit 0
}

if ($Action -eq 'Push') {
    $dirs = Get-ChildItem -Path $WorkDir -Directory -ErrorAction Stop
    if ($FileId) { $dirs = @($dirs | Where-Object { $_.Name -eq "$FileId" }) }
    foreach ($d in $dirs) {
        $meta  = Get-Content (Join-Path $d.FullName 'file.json') -Raw | ConvertFrom-Json
        $orig  = Join-Path $d.FullName 'original.docx'
        $work  = Join-Path $d.FullName 'work'
        $fixes = Join-Path $work 'fixes.json'
        $fixed = Join-Path $d.FullName 'fixed.docx'
        if (-not (Test-Path $fixes)) { Write-Output ("SKIP {0}: no fixes.json yet" -f $meta.display_name); continue }
        & python $py apply $orig --workdir $work --out $fixed
        if ($LASTEXITCODE -ne 0) { throw "apply failed for $($meta.display_name)" }
        & python $py verify $fixed
        if (-not $Apply) { Write-Output ("DRY RUN: would upload {0} over file id {1}" -f $fixed, $meta.id); continue }
        $size = (Get-Item $fixed).Length
        $slotBody = @{ name = $meta.display_name; size = $size; content_type = $DOCX_CT;
                       parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
        $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
        $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
        foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('-F', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
        $curlArgs += @('-F', ('file=@{0}' -f $fixed))
        $code = & curl.exe @curlArgs
        if ("$code" -notmatch '^(200|201|3..)$') { throw "upload failed ($code) for $($meta.display_name)" }
        Write-Output ("UPLOADED remediated {0} (original kept at {1})" -f $meta.display_name, $orig)
    }
    exit 0
}
