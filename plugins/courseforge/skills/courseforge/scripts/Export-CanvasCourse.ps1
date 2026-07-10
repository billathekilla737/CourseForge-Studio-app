<#
  Export-CanvasCourse.ps1 (courseforge) - export a whole course to a local
  .imscc (IMS Common Cartridge) backup file.

  Flow: POST /courses/:id/content_exports (export_type=common_cartridge)
        -> poll until exported -> download the attachment locally.

  FERPA note (document, don't discover): a common-cartridge export is COURSE
  CONTENT (pages, assignments, quizzes, files, modules). On a course that has
  been TAUGHT, Canvas can bundle student-authored discussion ENTRIES inside
  exported discussion topics. For other instructors' courses prefer exporting
  master/unpublished shells, and treat any .imscc from a taught course as a
  file that may contain student writing - keep it local, never commit it.
  (The .gitignore's canvas-export/ rule covers the default output folder.)

  Usage:
    .\Export-CanvasCourse.ps1                          # course from resolved config
    .\Export-CanvasCourse.ps1 -CourseId 739970
    .\Export-CanvasCourse.ps1 -ExportCourseId 721874   # any course the token can read
    .\Export-CanvasCourse.ps1 -OutDir .\backups

  -CourseId picks WHICH canvas.config.*.json supplies credentials;
  -ExportCourseId (optional) exports a DIFFERENT course than the config's
  (e.g. campus courses readable via an admin role) using the same token.

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$ExportCourseId,
    [string]$OutDir = '.\canvas-export\imscc',
    [ValidateSet('common_cartridge','zip')] [string]$ExportType = 'common_cartridge',
    [int]$TimeoutMinutes = 30
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$token = (Get-Content -Raw $ctx.TokenPath).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = if ($ExportCourseId) { $ExportCourseId } else { $cfg.course_id }
$hdr   = @{ Authorization = "Bearer $token" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# course name for the filename (and to confirm we're exporting what we think)
$course = Invoke-RestMethod -Uri "$base/api/v1/courses/$cid" -Headers $hdr
$safeName = ($course.name -replace '[^A-Za-z0-9._ -]', '_' -replace '\s+', '_')
Write-Host ("Exporting course {0}: {1}  (type: {2})" -f $cid, $course.name, $ExportType)

# 1) start the export (skip_notifications: no student-facing noise)
$export = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$cid/content_exports" -Headers $hdr `
    -Body @{ export_type = $ExportType; skip_notifications = 'true' }
$exportId = $export.id
Write-Host ("  export id {0} started ({1})" -f $exportId, $export.workflow_state)

# 2) poll until exported / failed
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$state = $export.workflow_state
while ($state -notin @('exported','failed')) {
    if ((Get-Date) -gt $deadline) { throw "Export timed out after $TimeoutMinutes minutes (state: $state)." }
    Start-Sleep -Seconds 5
    $export = Invoke-RestMethod -Uri "$base/api/v1/courses/$cid/content_exports/$exportId" -Headers $hdr
    $state = $export.workflow_state
    Write-Host ("  ...{0}" -f $state)
}
if ($state -eq 'failed') { throw "Canvas reported the export FAILED (export id $exportId)." }

# 3) download the attachment
$url = $export.attachment.url
if (-not $url) { throw "Export finished but no attachment URL was returned." }
$ext = if ($ExportType -eq 'zip') { 'zip' } else { 'imscc' }
$outFile = Join-Path $OutDir ("{0}_{1}_{2}.{3}" -f $cid, $safeName, (Get-Date -Format 'yyyyMMdd-HHmm'), $ext)
Invoke-WebRequest -Uri $url -OutFile $outFile -UseBasicParsing
$size = (Get-Item $outFile).Length
if ($size -lt 1024) { throw "Downloaded file is suspiciously small ($size bytes) - inspect $outFile" }

Write-Host ""
Write-Host ("EXPORTED -> {0}  ({1} KB)" -f $outFile, [math]::Round($size/1KB))
Write-Host "Keep this file local (it may contain student-authored discussion text on taught courses)."
