<#
  Batch-Remediate.ps1 (courseforge) - run the existing-course remediation
  pipeline (Dump -> restyle_html.py transform -> verify -> Push) across MANY
  courses in one go: the curriculum designer's actual job.

  For each course id it creates <WorkRoot>\<id>\ with its own
  canvas.config.<id>.json (+ a copy of the token, per the CanvasContext
  one-folder-per-course convention), runs the full pipeline, and rolls every
  course up into one aggregate table + batch-summary.md.

  SAFETY (inherits the pipeline's posture):
    - Push is DRY-RUN unless -Apply; verify must pass or that course's push is
      skipped entirely.
    - -ContinueOnError is the default: one bad course never blocks the rest
      (-StopOnError to abort the batch at the first failure).
    - Publish state and modules are never touched (Push-CanvasRemediation
      guarantees).

  Usage:
    .\Batch-Remediate.ps1 -CourseIds 721738,721956,722081                  # dry run
    .\Batch-Remediate.ps1 -CourseIds 721738,721956 -Look clean -Apply
    .\Batch-Remediate.ps1 -CourseIds 721738 -WorkRoot D:\rework

  Credentials: the base_url and token come from your resolved canvas.config
  (any of them - pass -ConfigPath/-CourseId to pick); the SAME token is used
  for every course in the batch (a designer's content-admin token works
  across courses).

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory=$true)] [int[]]$CourseIds,
    [string]$WorkRoot = '.\batch-remediation',
    [ValidateSet('hybrid','rich','clean')] [string]$Look = 'clean',   # ADA-safe default (~0 use-of-color flags); pass -Look hybrid/rich for a filled looks overhaul
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [switch]$Apply,
    [switch]$StopOnError
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx  = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$base = $ctx.Config.base_url.TrimEnd('/')

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$WorkRoot = (Resolve-Path $WorkRoot).Path

$rows = @()
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
Write-Host ("BATCH {0}: {1} course(s), look={2}{3}" -f
    $(if ($Apply) { 'APPLY' } else { 'DRY RUN' }), $CourseIds.Count, $Look,
    $(if ($Apply) { '' } else { '  (re-run with -Apply to write)' }))
Write-Host ""

foreach ($cid in $CourseIds) {
    $row = [ordered]@{ course = $cid; name = ''; items = 0; styled = 0; wrapped = 0
                       skipped = 0; fills = 0; verify = '-'; push = '-'; error = '' }
    $dir = Join-Path $WorkRoot "$cid"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    try {
        # per-course context folder (config + token side by side)
        $subCfg = Join-Path $dir ("canvas.config.{0}.json" -f $cid)
        if (-not (Test-Path $subCfg)) {
            $courseName = ''
            try {
                $hdrB = @{ Authorization = "Bearer $((Get-Content $ctx.TokenPath -Raw).Trim())" }
                $courseName = (Invoke-RestMethod -Uri "$base/api/v1/courses/$cid" -Headers $hdrB).name
            } catch {}
            [IO.File]::WriteAllText($subCfg,
                ('{{ "base_url": "{0}", "course_id": "{1}", "course_label": "{2}" }}' -f $base, $cid, ($courseName -replace '"','')),
                (New-Object Text.UTF8Encoding($false)))
        }
        Copy-Item $ctx.TokenPath (Join-Path $dir 'canvas.token') -Force
        $work = Join-Path $dir 'work'

        Write-Host ("--- course {0} ---" -f $cid)
        & "$PSScriptRoot\Dump-CanvasContent.ps1" -ConfigPath $subCfg -WorkDir $work | Out-Null
        if ($LASTEXITCODE) { throw "dump failed ($LASTEXITCODE)" }
        $manifest = Get-Content (Join-Path $work 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $row.name  = $manifest.course_label
        $row.items = @($manifest.items).Count

        & python "$PSScriptRoot\restyle_html.py" transform $work --look $Look | Out-Null
        if ($LASTEXITCODE) { throw "transform failed ($LASTEXITCODE)" }
        $manifest = Get-Content (Join-Path $work 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($it in @($manifest.items)) {
            switch ($it.transform_note) {
                'styled'        { $row.styled++ }
                'wrapped'       { $row.wrapped++ }
                'skipped-empty' { $row.skipped++ }
            }
            if ($it.fills_added) { $row.fills += [int]$it.fills_added }
        }

        & python "$PSScriptRoot\restyle_html.py" verify $work | Out-Null
        $row.verify = if ($LASTEXITCODE -eq 0) { 'PASS' } else { "FAIL($LASTEXITCODE)" }
        if ($LASTEXITCODE -ne 0) { throw "verify failed - push skipped" }

        $pushArgs = @{ WorkDir = $work; ConfigPath = $subCfg }
        if ($Apply) { $pushArgs.Apply = $true }
        & "$PSScriptRoot\Push-CanvasRemediation.ps1" @pushArgs | Out-Null
        if ($LASTEXITCODE) { throw "push reported failures ($LASTEXITCODE)" }
        $row.push = if ($Apply) { 'WRITTEN' } else { 'would-write' }
        Write-Host ("    ok: {0} items, verify {1}, push {2}" -f $row.items, $row.verify, $row.push)
    } catch {
        $row.error = $_.Exception.Message
        Write-Host ("    ERROR course {0}: {1}" -f $cid, $row.error)
        if ($StopOnError) { $rows += [pscustomobject]$row; break }
    }
    $rows += [pscustomobject]$row
}

# ---- aggregate ----------------------------------------------------------------
Write-Host ""
Write-Host "==== BATCH SUMMARY ===="
$rows | Format-Table course, name, items, styled, wrapped, skipped, fills, verify, push, error -AutoSize | Out-String | Write-Host

$md = @("# Batch remediation summary", "",
        ("Run {0} - look **{1}** - {2}" -f $stamp, $Look, $(if ($Apply) { '**APPLIED**' } else { 'dry run (nothing written)' })), "",
        "| Course | Name | Items | Styled | Wrapped | Skipped | Fills | Verify | Push | Error |",
        "|---|---|---|---|---|---|---|---|---|---|")
foreach ($r in $rows) {
    $md += ("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} | {9} |" -f
        $r.course, $r.name, $r.items, $r.styled, $r.wrapped, $r.skipped, $r.fills, $r.verify, $r.push, $r.error)
}
$md += @("", ("Total fills added (expected Ally 'use of color' advisories): {0}" -f (($rows | Measure-Object fills -Sum).Sum)),
         "Originals for every course are kept under each course's work\bodies\ (restorable).")
$mdPath = Join-Path $WorkRoot 'batch-summary.md'
[IO.File]::WriteAllText($mdPath, ($md -join "`r`n"), (New-Object Text.UTF8Encoding($false)))
Write-Host ("Summary: {0}" -f $mdPath)

$failed = @($rows | Where-Object { $_.error })
if ($failed.Count -gt 0) { exit 1 } else { exit 0 }
