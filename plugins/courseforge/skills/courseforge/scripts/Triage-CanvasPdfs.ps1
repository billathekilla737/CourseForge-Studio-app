<#
  Triage-CanvasPdfs.ps1 (courseforge) - inventory a course's PDFs and classify
  each for accessibility work. DETECTION ONLY - no file is modified or uploaded.

  Pairs with triage_pdf.py. Output: a ranked console table + triage-report.md
  in the workdir, worst first (scanned-image PDFs with no text layer are the
  biggest Ally score killers and need OCR / re-sourcing; text-untagged need
  manual tagging in Acrobat; tagged ones just need a spot check).

  Usage:
    .\Triage-CanvasPdfs.ps1 [-CourseId <id>] [-WorkDir .\pdf-triage]

  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$WorkDir = '.\pdf-triage'
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg   = $ctx.Config
$tok   = (Get-Content $ctx.TokenPath -Raw).Trim()
$base  = $cfg.base_url.TrimEnd('/')
$cid   = $cfg.course_id
$hdr   = @{ Authorization = "Bearer $tok" }
$py    = Join-Path $PSScriptRoot 'triage_pdf.py'

# list all PDFs (paginated)
$files = @()
$url = "$base/api/v1/courses/$cid/files?per_page=100&content_types[]=application/pdf"
while ($url) {
    $resp = Invoke-WebRequest -Uri $url -Headers $hdr -UseBasicParsing
    $files += ($resp.Content | ConvertFrom-Json)
    $url = $null
    if ($resp.Headers.Link) {
        foreach ($part in ($resp.Headers.Link -split ',')) {
            if ($part -match '<([^>]+)>;\s*rel="next"') { $url = $Matches[1] }
        }
    }
}
$files = @($files)
if ($files.Count -eq 0) { Write-Output "No PDFs in course $cid."; exit 0 }
Write-Output ("Course {0}: {1} PDF(s). Downloading for triage..." -f $cid, $files.Count)

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$local = @()
foreach ($f in $files) {
    $p = Join-Path $WorkDir ("{0}_{1}" -f $f.id, ($f.display_name -replace '[^A-Za-z0-9._-]','_'))
    Invoke-WebRequest -Uri $f.url -OutFile $p -UseBasicParsing
    $local += $p
}

$jsonOut = Join-Path $WorkDir 'triage.json'
& python $py @($local) --json $jsonOut
if ($LASTEXITCODE -ne 0) { throw "triage_pdf.py failed" }

# markdown report, worst first
$rows = Get-Content $jsonOut -Raw | ConvertFrom-Json
$md = @('# PDF accessibility triage - course ' + $cid, '',
        ('Generated ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + ' - DETECTION ONLY (no files changed).'), '',
        '| File | Class | Pages | What it needs |', '|---|---|---|---|')
foreach ($r in $rows) {
    $md += ('| {0} | **{1}** | {2} | {3} |' -f $r.file, $r.cls, $r.pages, $r.note)
}
$md += @('', 'Classes: **scanned-image** = no text layer, needs OCR / re-sourcing (worst for Ally);',
         '**text-untagged** = readable text but no headings/reading order for screen readers (manual Acrobat tagging);',
         '**tagged** = has tag structure, spot-check quality; **encrypted** = cannot inspect.')
$mdPath = Join-Path $WorkDir 'triage-report.md'
[IO.File]::WriteAllText($mdPath, ($md -join "`r`n"), (New-Object Text.UTF8Encoding($false)))
Write-Output ""
Write-Output ("Report: {0}" -f $mdPath)
