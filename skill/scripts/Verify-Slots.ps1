<#
  Verify-Slots.ps1
  The single most important safety check in this pipeline. Run it AFTER
  converting and BEFORE pushing.

  Why it exists: Notion page IDs in one workspace often share a long common
  prefix (e.g. 36f348e316178...). The Notion fetch tool occasionally resolves
  the WRONG same-prefix page, non-deterministically, scrambling content between
  pages -- sometimes across entirely different courses. A page can end up with
  a perfectly styled body that belongs to a different lesson.

  This compares each page's rendered <h2> hero title (which reflects the page
  actually fetched) against the title of the slot it was assigned to in the
  manifest. Any mismatch means content landed in the wrong place. When that
  happens, the correct bodies are usually already on disk under the wrong
  filenames -- reassemble by content rather than re-fetching (see SKILL.md).

  Usage:
    .\Verify-Slots.ps1 -Root "C:\path\to\project" -ManifestPaths .\canvas-export\manifest.12345.json,.\canvas-export\manifest.67890.json
  Exit code is the number of real mismatches (0 = safe to push).
#>
param(
  [Parameter(Mandatory)] [string]$Root,
  [Parameter(Mandatory)] [string[]]$ManifestPaths
)

function Norm($s) {
  ($s -replace '&amp;','&' -replace '&mdash;','-' -replace '&ndash;','-' `
      -replace '&ldquo;','"' -replace '&rdquo;','"' -replace '[^a-zA-Z0-9]','').ToLower()
}

$bad = 0; $tot = 0
foreach ($mf in $ManifestPaths) {
  $m = Get-Content -Raw -Encoding UTF8 $mf | ConvertFrom-Json
  Write-Host "=== $([System.IO.Path]::GetFileName($mf))  ($($m.pages.Count) pages) ==="
  foreach ($p in $m.pages) {
    $tot++
    $full = Join-Path $Root $p.file
    if (-not (Test-Path $full)) { $bad++; Write-Host "  MISSING FILE: $($p.file)"; continue }
    $txt  = [System.IO.File]::ReadAllText($full)
    $hero = ([regex]::Match($txt, '<h2[^>]*>(.*?)</h2>')).Groups[1].Value
    $nh = Norm $hero; $nt = Norm $p.title
    # Accept exact match, substring either way (heroes sometimes drop an
    # "Assignment -"/"Project -" prefix), and the syllabus (titled "Course
    # Syllabus" but whose hero keeps the full Notion title).
    $ok = ($nh -eq $nt) -or ($nt -like "*$nh*") -or ($nh -like "*$nt*") -or `
          ($p.title -eq 'Course Syllabus' -and $hero -match 'Syllabus')
    if (-not $ok) { $bad++; Write-Host ("  MISMATCH  slot='{0}'  hero='{1}'  [{2}]" -f $p.title, $hero, $p.notion_id) }
  }
}
Write-Host ""
Write-Host ("{0}/{1} pages match their slot.  {2} mismatch(es)." -f ($tot - $bad), $tot, $bad)
if ($bad -gt 0) { Write-Host "DO NOT PUSH until these are resolved (see SKILL.md 'When verification fails')." }
exit $bad
