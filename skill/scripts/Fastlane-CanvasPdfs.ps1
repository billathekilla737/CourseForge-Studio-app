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
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = Get-Content $ctx.ConfigPath -Raw | ConvertFrom-Json
$tok   = $ctx.Token
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $tok" }
$py    = Join-Path $PSScriptRoot 'pdf_fastlane.py'

# External programs this script shells out to. Both are assumed present
# everywhere; check up front so a missing one is a one-line explanation
# instead of a confusing native-command failure part-way through a run.
function Assert-Tool([string]$Exe, [string]$Needed, [string]$Fix) {
    $hit = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $hit) { throw ("{0} was not found on PATH. {1} needs it. {2}" -f $Exe, $Needed, $Fix) }
    return $hit.Source
}
if ($Action -in @('Fetch','Push')) {
    # curl.exe ships with Windows 10 1803+ and Server 2019+
    Assert-Tool 'curl.exe' ("Fastlane -Action " + $Action) `
        'It ships with Windows 10 1803 and newer; on an older build install curl or use the desktop app instead.' | Out-Null
}
if ($Action -eq 'Process') {
    Assert-Tool 'python' 'Fastlane -Action Process' `
        'Install Python 3.10+ and make sure `python` is on PATH, or use the CourseForge PDF Fixer desktop app (it bundles its own).' | Out-Null
    if (-not (Test-Path $py)) { throw ("pdf_fastlane.py not found next to this script: {0}" -f $py) }
}

function Get-CoursePdfs {
    # unfiltered fetch + client-side match: a server content_types[] filter
    # silently drops PDFs uploaded with an empty/octet-stream content_type
    # Get-CanvasPaged (CanvasContext.ps1) instead of a private loop: it has the
    # timeout, the throttle backoff and the non-advancing-next-link guard.
    $out = @(Get-CanvasPaged -Url "$base/api/v1/courses/$cid/files?per_page=100" -Headers $hdr)
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
    # Canvas file urls are pre-signed (verifier token), so ONE curl --parallel
    # run downloads everything - on high-latency links this is the difference
    # between 43 sequential round-trips and 6 concurrent streams
    $cfg = New-Object Text.StringBuilder
    $n = 0; $queued = 0
    foreach ($f in $files) {
        $d = Join-Path $WorkDir "$($f.id)"
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $orig = Join-Path $d 'original.pdf'
        if (-not (Test-Path $orig)) {
            [void]$cfg.AppendLine("url = `"$($f.url)`"")
            [void]$cfg.AppendLine("output = `"$orig`"")
            $queued++
        }
        @{ id = $f.id; display_name = $f.display_name; folder_id = $f.folder_id;
           size = $f.size } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $d 'file.json') -Encoding UTF8
        $n++
    }
    if ($queued -gt 0) {
        # This config file holds Canvas PRE-SIGNED urls - each one grants
        # access to that file without a token. Write it to the user's own temp
        # dir (not the shared workdir) and delete it in a finally, so a thrown
        # error cannot leave signed urls lying around.
        $cfgPath = Join-Path ([IO.Path]::GetTempPath()) ("cf-fetch-{0}.cfg" -f [guid]::NewGuid().ToString('N'))
        try {
            Set-Content -Path $cfgPath -Value $cfg.ToString() -Encoding ASCII
            & curl.exe -sS -L --parallel --parallel-max 6 --retry 3 -K $cfgPath
            if ($LASTEXITCODE -ne 0) { Write-Warning "curl reported errors (code $LASTEXITCODE) - re-run Fetch; existing files are kept" }
        } finally {
            Remove-Item $cfgPath -Force -Confirm:$false -ErrorAction SilentlyContinue
        }
        # A dropped connection leaves a SHORT original.pdf that every later run
        # treats as a good backup. Compare against the size Canvas reported and
        # delete anything that does not match, so Fetch can retry it.
        foreach ($f in $files) {
            $orig = Join-Path (Join-Path $WorkDir "$($f.id)") 'original.pdf'
            if (-not (Test-Path $orig)) { continue }
            if ($f.size -and ((Get-Item $orig).Length -ne [int64]$f.size)) {
                Write-Warning ("{0} downloaded incompletely ({1} of {2} bytes) - removed; re-run Fetch" -f `
                    $f.display_name, (Get-Item $orig).Length, $f.size)
                Remove-Item $orig -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $have = @(Get-ChildItem $WorkDir -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'original.pdf') }).Count
    Write-Output ("Fetched {0} PDF(s) into {1} ({2} downloaded now, {3} present total)" -f $n, $WorkDir, $queued, $have)
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
    $up = 0; $skip = 0; $failed = 0; $pending = @()
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
        # 3-step Canvas upload: request the slot now (auth + latency-bound),
        # queue the heavy multipart POST for the parallel phase below
        $size = (Get-Item $fixed).Length
        $slotBody = @{ name = $meta.display_name; size = $size; content_type = 'application/pdf';
                       parent_folder_id = $meta.folder_id; on_duplicate = 'overwrite' }
        $slot = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/files" -Headers $hdr -Body $slotBody
        $curlArgs = @('-sS','-o','NUL','-w','%{http_code}','-X','POST',$slot.upload_url)
        foreach ($k in $slot.upload_params.PSObject.Properties.Name) { $curlArgs += @('--form-string', ('{0}={1}' -f $k, $slot.upload_params.$k)) }
        $curlArgs += @('-F', ('file=@{0}' -f $fixed))
        $pending += ,@{ name = $meta.display_name; args = $curlArgs; orig = (Join-Path $d.FullName 'original.pdf') }
    }
    # parallel multipart uploads, 6 at a time - on a high-latency link the
    # per-file handshake cost amortizes across concurrent streams
    $batchSize = 6
    for ($i = 0; $i -lt $pending.Count; $i += $batchSize) {
        $batch = $pending[$i..([Math]::Min($i+$batchSize,$pending.Count)-1)]
        $procs = @()
        foreach ($u in $batch) {
            $outFile = [IO.Path]::GetTempFileName()
            # Quote EVERY argument and escape any embedded double quote the way
            # the Windows CRT expects. The old version quoted only arguments
            # matching \s|@|= and did not escape at all, so a Canvas
            # display_name containing a " (they are allowed) broke the command
            # line apart. Quoting unconditionally is also simply less to think
            # about than deciding which values are "safe".
            $quoted = @($u.args | ForEach-Object { '"' + ([string]$_ -replace '"', '\"') + '"' })
            $p = Start-Process -FilePath 'curl.exe' -ArgumentList $quoted `
                 -NoNewWindow -PassThru -RedirectStandardOutput $outFile
            $procs += ,@{ p = $p; u = $u; out = $outFile }
        }
        foreach ($j in $procs) {
            $j.p.WaitForExit()
            $code = (Get-Content $j.out -Raw -ErrorAction SilentlyContinue)
            if ($null -ne $code) { $code = $code.Trim() }
            Remove-Item $j.out -Force -Confirm:$false -ErrorAction SilentlyContinue
            if ("$code" -match '^(200|201|3..)$') {
                Write-Output ("UPLOADED {0} (original kept at {1})" -f $j.u.name, $j.u.orig)
                $up++
            } else {
                Write-Output ("FAILED   {0} (http {1}) - re-run Push; its slot has expired" -f $j.u.name, $code)
                # COUNT it. Failures used to print and vanish, and the script
                # then exited 0 - so "every upload failed" looked like success
                # to anything reading the exit code.
                $failed++
            }
        }
    }
    Write-Output ("Push done: {0} uploaded, {1} failed, {2} skipped." -f $up, $failed, $skip)
    if ($failed -gt 0) {
        Write-Output "Re-run -Action Push -Apply to retry the failures (each needs a fresh upload slot)."
        exit 1
    }
    exit 0
}
