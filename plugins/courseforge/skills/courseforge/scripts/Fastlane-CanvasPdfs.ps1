<#
  Fastlane-CanvasPdfs.ps1 (courseforge) - FAST deterministic PDF ADA pipeline
  for a whole course. The speed layer: pdf_fastlane.py does the mechanical 90%
  (OCR text layers, basic-but-real tag trees, title/language metadata) in
  parallel with no model round-trips; whatever it cannot fix with confidence
  lands in WorkDir\queue.json for the model to handle one by one.

  Actions:
    1) -Action List     : enumerate the course's PDFs
    2) -Action Fetch    : download each into WorkDir\<fileId>\original.pdf
                          (+ file.json metadata) - originals are the backups
    3) -Action Process  : python batch -> fixed.pdf + result.json per file,
                          summary.json + queue.json at the top. Exit 2 when
                          anything queued as an error.
    4) -Action Push     : upload each VERIFIED fixed.pdf over its original
                          (same folder + name, on_duplicate=overwrite, so all
                          course links keep working). DRY-RUN by default;
                          -Apply to upload. Files whose result.json is not
                          ok/review are never uploaded.

  Model fallback protocol (after Process):
    - read WorkDir\queue.json; each entry names the file, why the fast path
      declined, and a hint
    - fix the FILE (surgical: pdf_fastlane.py process on a corrected input,
      or the other pdf tools), OR when one reason repeats across files, fix
      the PROGRAM (patch pdf_fastlane.py, run `python pdf_fastlane.py
      selftest` until PASS, then re-run -Action Process - it only reprocesses
      what has no ok result yet unless -Force)

  Course FILES are course content (not student data); canvas-pii-guard still
  gates every endpoint. ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Fetch','Process','Push')] [string]$Action,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\pdf-fastlane',
    [string]$FileId,          # limit Fetch/Process/Push to one file
    [int]$Jobs = 0,           # 0 = auto (cpu-2)
    [switch]$Force,           # Process: redo files that already have a result
    [switch]$Apply            # Push without -Apply = dry run
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = Get-Content $ctx.ConfigPath -Raw | ConvertFrom-Json
$tok   = (Get-Content $ctx.TokenPath -Raw).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $tok" }
$py    = Join-Path $PSScriptRoot 'pdf_fastlane.py'

function Get-CoursePdfs {
    # unfiltered fetch + client-side match: a server content_types[] filter
    # silently drops PDFs uploaded with an empty/octet-stream content_type
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
    $out = @($out | Where-Object { $_.display_name -match '(?i)\.pdf$' -or $_.content_type -eq 'application/pdf' })
    if ($FileId) { $out = @($out | Where-Object { "$($_.id)" -eq "$FileId" }) }
    return $out
}

if ($Action -eq 'List') {
    $files = @(Get-CoursePdfs)
    Write-Output ("Found {0} PDF(s) in course {1}:" -f $files.Count, $cid)
    foreach ($f in $files) {
        Write-Output ("  id={0}  {1}  ({2} KB)" -f $f.id, $f.display_name, [math]::Round($f.size/1KB))
    }
    exit 0
}

if ($Action -eq 'Fetch') {
    $files = @(Get-CoursePdfs)
    if ($files.Count -eq 0) { Write-Output "No PDFs to fetch."; exit 0 }
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    $n = 0
    foreach ($f in $files) {
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d 'original.pdf'
        if (-not (Test-Path $orig)) {
            Invoke-WebRequest -Uri $f.url -OutFile $orig -UseBasicParsing
        }
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id;
           size = $f.size } | ConvertTo-Json | Set-Content (Join-Path $d 'file.json') -Encoding UTF8
        $n++
    }
    Write-Output ("Fetched {0} PDF(s) into {1}" -f $n, $WorkDir)
    exit 0
}

if ($Action -eq 'Process') {
    if (-not (Test-Path $WorkDir)) { throw "WorkDir $WorkDir not found - run -Action Fetch first" }
    if ($Force) {
        Get-ChildItem $WorkDir -Directory | ForEach-Object {
            Remove-Item (Join-Path $_.FullName 'fixed.pdf'), (Join-Path $_.FullName 'result.json') -ErrorAction SilentlyContinue
        }
    }
    $pyArgs = @('batch', '--workdir', $WorkDir)
    if ($Jobs -gt 0) { $pyArgs += @('--jobs', "$Jobs") }
    & python $py @pyArgs
    $rc = $LASTEXITCODE
    $qPath = Join-Path $WorkDir 'queue.json'
    if (Test-Path $qPath) {
        $q = Get-Content $qPath -Raw | ConvertFrom-Json
        if (@($q).Count -gt 0) {
            Write-Output ""
            Write-Output "FALLBACK QUEUE ($(@($q).Count)) - model attention needed:"
            foreach ($e in $q) {
                Write-Output ("  [{0}] {1}: {2}" -f $e.severity, $e.file, $e.reason)
            }
        }
    }
    exit $rc
}

if ($Action -eq 'Push') {
    $dirs = Get-ChildItem -Path $WorkDir -Directory
    if ($FileId) { $dirs = @($dirs | Where-Object { $_.Name -eq "$FileId" }) }
    $up = 0; $skip = 0
    foreach ($d in $dirs) {
        $metaPath = Join-Path $d.FullName 'file.json'
        $resPath  = Join-Path $d.FullName 'result.json'
        $fixed    = Join-Path $d.FullName 'fixed.pdf'
        if (-not (Test-Path $metaPath)) { continue }
        $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
        if (-not (Test-Path $resPath) -or -not (Test-Path $fixed)) {
            Write-Output ("SKIP {0}: no verified fixed.pdf" -f $meta.display_name); $skip++; continue
        }
        $res = Get-Content $resPath -Raw | ConvertFrom-Json
        if ($res.status -notin @('ok','review')) {
            Write-Output ("SKIP {0}: status={1}" -f $meta.display_name, $res.status); $skip++; continue
        }
        if (-not $Apply) {
            Write-Output ("DRY RUN: would upload {0} over file id {1} ({2})" -f $meta.display_name, $meta.id, ($res.actions -join '; '))
            continue
        }
        # 3-step Canvas upload, same name + folder, overwrite (links keep working)
        $size = (Get-Item $fixed).Length
        $slotBody = @{ name = $meta.display_name; size = $size; content_type = 'application/pdf';
                       parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
        $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
        $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
        foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('-F', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
        $curlArgs += @('-F', ('file=@{0}' -f $fixed))
        $code = & curl.exe @curlArgs
        if ("$code" -notmatch '^(200|201|3..)$') { throw "upload failed ($code) for $($meta.display_name)" }
        Write-Output ("UPLOADED {0} (original kept at {1})" -f $meta.display_name, (Join-Path $d.FullName 'original.pdf'))
        $up++
    }
    Write-Output ("Push done: {0} uploaded, {1} skipped." -f $up, $skip)
    exit 0
}
