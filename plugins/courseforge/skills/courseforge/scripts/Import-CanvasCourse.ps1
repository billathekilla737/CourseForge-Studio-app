<#
  Import-CanvasCourse.ps1 (courseforge) - import content INTO a Canvas course:
  clone another course (the "replica sandbox" flow) or restore an .imscc backup.

  Two sources (pick exactly one):
    -SourceCourseId <id>   Canvas-to-Canvas COPY (course_copy_importer). The token
                           must be able to read the source (admin roles can).
    -ImsccPath <file>      Upload + import a local .imscc/.zip backup
                           (common_cartridge_importer via the 3-step file upload).

  Destination (pick one):
    (default)              the course from the resolved canvas.config
    -DestCourseId <id>     a specific existing course
    -NewCourseName <name>  CREATE a fresh unpublished shell first (needs
                           -AccountId; requires an admin role that can create
                           courses in that account), then import into it.

  SAFETY:
    - Import ADDS content. If the destination already has pages or modules,
      this script REFUSES unless -Force (prevents accidental duplication into
      a populated course).
    - The new-shell path creates the course UNPUBLISHED and never publishes.
    - Dry-run by default: shows source, destination, and mode. -Apply to run.

  Usage:
    .\Import-CanvasCourse.ps1 -SourceCourseId 721874 -DestCourseId 739972 -Apply
    .\Import-CanvasCourse.ps1 -SourceCourseId 721874 -NewCourseName "SANDBOX - IMT2772 replica" -AccountId 10626 -Apply
    .\Import-CanvasCourse.ps1 -ImsccPath .\backups\721874.imscc -DestCourseId 739972 -Apply

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$SourceCourseId,
    [string]$ImsccPath,
    [string]$DestCourseId,
    [string]$NewCourseName,
    [string]$AccountId,
    [int]$TimeoutMinutes = 30,
    [switch]$Force,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (($SourceCourseId -and $ImsccPath) -or (-not $SourceCourseId -and -not $ImsccPath)) {
    throw "Pick exactly one source: -SourceCourseId <id> (course copy) OR -ImsccPath <file> (.imscc import)."
}
if ($ImsccPath -and -not (Test-Path $ImsccPath)) { throw "File not found: $ImsccPath" }
if ($NewCourseName -and -not $AccountId) { throw "-NewCourseName requires -AccountId (where to create the shell)." }

. "$PSScriptRoot\CanvasContext.ps1"
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$token = (Get-Content -Raw $ctx.TokenPath).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$hdr   = @{ Authorization = "Bearer $token" }

function Get-JsonArray([string]$url) {
    # PS 5.1 footgun (measured): @(Invoke-RestMethod ...) around the CMDLET nests
    # the JSON array (Count=1), and `return Invoke-RestMethod ...` propagates the
    # same single-object. The ONLY reliable shape is assign-to-variable first,
    # then return @($var) - that enumerates elements so @(Get-JsonArray ...)
    # counts correctly. (foreach loops unwrap either way; .Count does not.)
    $d = Invoke-RestMethod -Uri $url -Headers $hdr
    return @($d)
}

# --- resolve destination -------------------------------------------------------
$destId = $DestCourseId
if (-not $destId -and -not $NewCourseName) { $destId = [string]$cfg.course_id }

$mode = if ($SourceCourseId) { 'course copy' } else { '.imscc import' }
$src  = if ($SourceCourseId) { "course $SourceCourseId" } else { $ImsccPath }
$dst  = if ($NewCourseName) { "NEW shell '$NewCourseName' in account $AccountId" } else { "course $destId" }
Write-Host ("{0}: {1}  ->  {2}" -f $mode.ToUpper(), $src, $dst)

if (-not $Apply) {
    Write-Host ""
    Write-Host "DRY RUN - nothing created or imported. Re-run with -Apply to execute."
    exit 0
}

# --- create the shell if asked --------------------------------------------------
if ($NewCourseName) {
    try {
        $newCourse = Invoke-RestMethod -Method Post -Uri "$base/api/v1/accounts/$AccountId/courses" -Headers $hdr `
            -Body @{ 'course[name]' = $NewCourseName; 'course[course_code]' = $NewCourseName }
    } catch {
        $status = ''
        try { $status = [int]$_.Exception.Response.StatusCode } catch {}
        if ("$status" -eq '403') {
            throw ("Your Canvas role cannot CREATE courses in account {0} (403). Content-only admin " +
                   "roles typically read campus courses but cannot provision shells. Have a full admin " +
                   "create the shell in the Canvas UI, then re-run with -DestCourseId <its id>.") -f $AccountId
        }
        throw
    }
    $destId = $newCourse.id
    Write-Host ("  created unpublished shell id {0}: {1}" -f $destId, $newCourse.name)
}

# --- populated-destination gate --------------------------------------------------
$destPages   = @(Get-JsonArray "$base/api/v1/courses/$destId/pages?per_page=5")
$destModules = @(Get-JsonArray "$base/api/v1/courses/$destId/modules?per_page=5")
if (($destPages.Count -gt 0 -or $destModules.Count -gt 0) -and -not $Force) {
    throw ("Destination course {0} already has content ({1}+ pages, {2}+ modules). " +
           "Importing would ADD/duplicate on top of it. Re-run with -Force if that is intended.") `
           -f $destId, $destPages.Count, $destModules.Count
}

# --- start the migration ----------------------------------------------------------
if ($SourceCourseId) {
    $mig = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$destId/content_migrations" -Headers $hdr `
        -Body @{ migration_type = 'course_copy_importer'; 'settings[source_course_id]' = $SourceCourseId }
} else {
    $file = Get-Item $ImsccPath
    $mig = Invoke-RestMethod -Method Post -Uri "$base/api/v1/courses/$destId/content_migrations" -Headers $hdr `
        -Body @{ migration_type = 'common_cartridge_importer'
                 'pre_attachment[name]' = $file.Name
                 'pre_attachment[size]' = [string]$file.Length }
    $pre = $mig.pre_attachment
    if (-not $pre -or -not $pre.upload_url) { throw "Canvas did not return an upload slot for the cartridge." }
    # step 2: multipart upload via curl.exe (PS 5.1-safe)
    $curlArgs = @('-s','-o','NUL','-w','%{http_code}','-X','POST',$pre.upload_url)
    foreach ($k in $pre.upload_params.PSObject.Properties.Name) {
        $curlArgs += @('-F', ('{0}={1}' -f $k, $pre.upload_params.$k))
    }
    $curlArgs += @('-F', ('file=@{0}' -f $file.FullName))
    $code = & curl.exe @curlArgs
    if ("$code" -notmatch '^(200|201|3..)$') { throw "Cartridge upload failed (HTTP $code)." }
    Write-Host "  cartridge uploaded"
}
$migId = $mig.id
Write-Host ("  migration id {0} started ({1})" -f $migId, $mig.workflow_state)

# --- poll to completion ------------------------------------------------------------
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$state = $mig.workflow_state
while ($state -notin @('completed','failed')) {
    if ((Get-Date) -gt $deadline) { throw "Migration timed out after $TimeoutMinutes minutes (state: $state)." }
    Start-Sleep -Seconds 5
    $mig = Invoke-RestMethod -Uri "$base/api/v1/courses/$destId/content_migrations/$migId" -Headers $hdr
    $state = $mig.workflow_state
    Write-Host ("  ...{0}" -f $state)
}
if ($state -eq 'failed') {
    $issues = ''
    try { $issues = ($mig.migration_issues_count) } catch {}
    throw ("Migration FAILED (id {0}; issues: {1}). Check the course's Import Content page for details." -f $migId, $issues)
}

# --- report what landed --------------------------------------------------------------
$pages   = @(Get-JsonArray "$base/api/v1/courses/$destId/pages?per_page=100")
$modules = @(Get-JsonArray "$base/api/v1/courses/$destId/modules?per_page=100")
$assigns = @(Get-JsonArray "$base/api/v1/courses/$destId/assignments?per_page=100")
Write-Host ""
Write-Host ("IMPORT COMPLETE -> course {0}: {1} pages, {2} modules, {3} assignments now present." -f $destId, $pages.Count, $modules.Count, $assigns.Count)
Write-Host ("Open it: {0}/courses/{1}" -f $base, $destId)
Write-Host "Publish state was NOT touched (new shells stay unpublished)."
