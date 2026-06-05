<#
  Get-CanvasData-Sterilized.ps1  (canvas-pii-guard)
  The ONLY sanctioned way to pull Canvas data when data is genuinely needed. It:
    1. fetches the raw response locally,
    2. writes the RAW result to a gitignored private/ folder (the agent must never read it),
    3. emits to stdout ONLY a STERILIZED (PII-redacted, Strict profile) rendering.
  The block hook permits this script by name; ad-hoc Canvas data calls remain blocked.

  This reduces risk; it is NOT a certified-clean guarantee for free-text PII. Prefer
  aggregates. ASCII only.

  Usage:
    .\Get-CanvasData-Sterilized.ps1 -ConfigPath ..\canvas.config.<id>.json -Path "/assignments?per_page=100"
#>
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$Path,
    [string]$TokenPath,
    [string]$OutDir
)
. "$PSScriptRoot\PiiPatterns.ps1"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
if (-not $TokenPath) { $TokenPath = Join-Path (Split-Path $ConfigPath -Parent) 'canvas.token' }
if (-not $OutDir)    { $OutDir    = Join-Path (Split-Path $ConfigPath -Parent) 'private' }
$token   = (Get-Content -Raw $TokenPath).Trim()
$base    = $cfg.base_url.TrimEnd('/')
$cid     = $cfg.course_id
$headers = @{ Authorization = "Bearer $token" }
$uri     = "$base/api/v1/courses/$cid$Path"

New-Item -ItemType Directory -Force $OutDir | Out-Null
$resp = Invoke-RestMethod -Uri $uri -Headers $headers -Method GET -ErrorAction Stop
$rawJson = $resp | ConvertTo-Json -Depth 12

# 1) write RAW locally (private; never read by the agent)
$safe = ($Path -replace '[\\/:*?"<>|=&]', '_').Trim('_')
if (-not $safe) { $safe = 'data' }
$rawFile = Join-Path $OutDir ("{0}.raw.json" -f $safe)
[IO.File]::WriteAllText($rawFile, $rawJson)

# 2) sterilize (Strict) and emit only that
$ster = Invoke-PiiRedaction -Text $rawJson -Profile 'Strict'
Write-Host "STERILIZED OUTPUT (PII redacted; $($ster.Count) pattern(s) scrubbed; raw kept private at $rawFile):"
Write-Host $ster.Text
