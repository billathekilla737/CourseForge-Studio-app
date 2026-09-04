<#
  sync-skill.ps1 - keep the repo's skill\ copy and the LIVE skill in step.

  The source of truth while you work is the live skill that Claude Code loads:
      %USERPROFILE%\.claude\skills\courseforge\
  The repo keeps its copy under plugins\courseforge\skills\courseforge\ so the code is actually IN the
  repository and buildable by anyone who clones it. Two copies can drift, so
  this script is the only sanctioned way to move between them.

      .\sync-skill.ps1                 # show what differs (default: no writes)
      .\sync-skill.ps1 -ToRepo         # live  -> repo plugin copy   (before a commit)
      .\sync-skill.ps1 -ToLive         # repo plugin copy -> live    (after a pull)

  __pycache__, .pyc and any stray credential file are never copied.
  ASCII only. PowerShell 5.1 compatible.
#>
param(
    [switch]$ToRepo,
    [switch]$ToLive,
    [string]$LiveRoot = (Join-Path $env:USERPROFILE '.claude\skills\courseforge'),
    [string]$RepoRoot = (Join-Path $PSScriptRoot 'plugins\courseforge\skills\courseforge')
)

$ErrorActionPreference = 'Stop'

if ($ToRepo -and $ToLive) { throw "Pick one direction: -ToRepo or -ToLive." }

$EXCLUDE_DIRS  = @('__pycache__', '.git', '.pytest_cache', '.ruff_cache')
$EXCLUDE_FILES = @('*.pyc', '*.pyo', 'canvas.token', 'canvas.token.enc',
                   '*.token', '*.token.enc', 'canvas.config.*.json',
                   'activity-log.jsonl')

function Get-SkillFiles([string]$root) {
    if (-not (Test-Path $root)) { return @{} }
    $map = @{}
    Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        $parts = $rel -split '\\'
        foreach ($d in $EXCLUDE_DIRS) { if ($parts -contains $d) { return } }
        foreach ($pat in $EXCLUDE_FILES) { if ($_.Name -like $pat) { return } }
        $map[$rel] = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash
    }
    return $map
}

$live = Get-SkillFiles $LiveRoot
$repo = Get-SkillFiles $RepoRoot

$onlyLive = @($live.Keys | Where-Object { -not $repo.ContainsKey($_) } | Sort-Object)
$onlyRepo = @($repo.Keys | Where-Object { -not $live.ContainsKey($_) } | Sort-Object)
$differ   = @($live.Keys | Where-Object { $repo.ContainsKey($_) -and $repo[$_] -ne $live[$_] } | Sort-Object)

Write-Host ''
Write-Host ("live: {0}  ({1} file(s))" -f $LiveRoot, $live.Count)
Write-Host ("repo: {0}  ({1} file(s))" -f $RepoRoot, $repo.Count)
Write-Host ''

if (-not $onlyLive -and -not $onlyRepo -and -not $differ) {
    Write-Host 'IN SYNC - nothing to do.' -ForegroundColor Green
    exit 0
}
if ($onlyLive) { Write-Host ("only in live ({0}):" -f $onlyLive.Count) -ForegroundColor Yellow; $onlyLive | ForEach-Object { Write-Host "   + $_" } }
if ($onlyRepo) { Write-Host ("only in repo ({0}):" -f $onlyRepo.Count) -ForegroundColor Yellow; $onlyRepo | ForEach-Object { Write-Host "   - $_" } }
if ($differ)   { Write-Host ("different ({0}):"    -f $differ.Count)   -ForegroundColor Yellow; $differ   | ForEach-Object { Write-Host "   ~ $_" } }

if (-not $ToRepo -and -not $ToLive) {
    Write-Host ''
    Write-Host 'Nothing was written. Re-run with -ToRepo (live -> repo) or -ToLive (repo -> live).'
    exit 1
}

$src = if ($ToRepo) { $LiveRoot } else { $RepoRoot }
$dst = if ($ToRepo) { $RepoRoot } else { $LiveRoot }
$srcMap = if ($ToRepo) { $live } else { $repo }

Write-Host ''
Write-Host ("Copying {0} -> {1}" -f $src, $dst) -ForegroundColor Cyan
if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Force $dst | Out-Null }
$n = 0
foreach ($rel in ($srcMap.Keys | Sort-Object)) {
    $s = Join-Path $src $rel
    $d = Join-Path $dst $rel
    $dDir = Split-Path -Parent $d
    if (-not (Test-Path $dDir)) { New-Item -ItemType Directory -Force $dDir | Out-Null }
    Copy-Item -LiteralPath $s -Destination $d -Force
    $n++
}
# remove files that no longer exist on the source side
$gone = if ($ToRepo) { $onlyRepo } else { $onlyLive }
foreach ($rel in $gone) {
    $d = Join-Path $dst $rel
    if (Test-Path $d) { Remove-Item -Force $d; Write-Host "   deleted $rel" }
}
Write-Host ("Synced {0} file(s), removed {1}." -f $n, $gone.Count) -ForegroundColor Green
Write-Host 'Re-run with no switches to confirm they now match.'
exit 0
