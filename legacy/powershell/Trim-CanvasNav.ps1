<#
  Trim-CanvasNav.ps1
  Hide the institutional bloat from a course's left navigation, leaving only a
  chosen keep-list, in order.

  CRITICAL: the Canvas tabs endpoint SILENTLY IGNORES form-encoded bodies
  (returns 200, changes nothing). The body MUST be JSON. That is the whole
  reason this script exists instead of a one-liner.

  Usage:
    .\Trim-CanvasNav.ps1                         # DRY RUN on the connected course
    .\Trim-CanvasNav.ps1 -Apply                  # trim the connected course's navigation
    .\Trim-CanvasNav.ps1 -CourseIds 12345,67890 -Apply   # other courses on the SAME Canvas site

  The default -Keep is one school's layout. Override it with your own ordered
  map of tab-id -> position. Find tab ids with:
    GET /api/v1/courses/:id/tabs   (LTI tools look like context_external_tool_NNN)
#>
param(
  [string]$BaseUrl = '',       # optional; must be the connected course's own Canvas site
  [int[]]$CourseIds = @(),     # default: the connected course only
  [string]$ConfigPath = '',    # resolved by CanvasContext.ps1 when omitted
  [string]$TokenPath = '',
  [System.Collections.IDictionary]$Keep = ([ordered]@{
    'home'=1; 'announcements'=2; 'syllabus'=3; 'modules'=4;
    'context_external_tool_382357'=5;  'discussions'=6; 'grades'=7; 'people'=8; 'files'=9;
    'context_external_tool_221916'=10; 'context_external_tool_342011'=11
  }),
  [switch]$WhatIf,
  [switch]$Apply               # without it this is a dry run: nothing is changed
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
. "$PSScriptRoot\CanvasContext.ps1"
$ctx   = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath
$token = $ctx.Token
# The token is only ever sent to the site it was saved for. A -BaseUrl that
# names any other host is refused rather than honoured.
$base  = $ctx.Config.base_url.TrimEnd('/')
if ($BaseUrl -and ($BaseUrl.TrimEnd('/').ToLower() -ne $base.ToLower())) {
    throw ("-BaseUrl {0} is not the connected course's Canvas site ({1})." -f $BaseUrl, $base)
}
if (-not $CourseIds -or $CourseIds.Count -eq 0) { $CourseIds = @([int]$ctx.Config.course_id) }
if (-not $Apply) { $WhatIf = $true; Write-Host "[WhatIf] DRY RUN - nothing is changed (pass -Apply to trim the navigation)" }
$headers = @{ Authorization = "Bearer $token" }

function Set-Tab($cid, $tid, $obj) {
  Invoke-RestMethod -Uri "$base/api/v1/courses/$cid/tabs/$tid" -Headers $headers `
    -Method Put -Body ($obj | ConvertTo-Json -Depth 10) -ContentType 'application/json' -ErrorAction Stop | Out-Null
}

foreach ($cid in $CourseIds) {
  $tabs = Invoke-RestMethod -Uri "$base/api/v1/courses/$cid/tabs" -Headers $headers
  foreach ($t in $tabs) {
    # Home and Settings can't be hidden (Settings is teacher-only anyway).
    if ((-not $Keep.Contains($t.id)) -and ($t.id -ne 'settings')) {
      if ($WhatIf) { Write-Host "  [WhatIf] hide $($t.label)" }
      else { try { Set-Tab $cid $t.id @{ hidden = $true } } catch { Write-Host "  ! hide $($t.label): $($_.Exception.Message)" } }
    }
  }
  foreach ($id in ($Keep.Keys)) {
    if ($id -eq 'home') { continue }
    if ($WhatIf) { Write-Host "  [WhatIf] show+order $id -> pos $($Keep[$id])" }
    else { try { Set-Tab $cid $id @{ hidden = $false; position = $Keep[$id] } } catch { Write-Host "  ! show ${id}: $($_.Exception.Message)" } }
  }
  if (-not $WhatIf) {
    $after = Invoke-RestMethod -Uri "$base/api/v1/courses/$cid/tabs" -Headers $headers
    $vis = ($after | Where-Object { -not $_.hidden -and $_.id -ne 'settings' } | Sort-Object position | ForEach-Object { $_.label }) -join '  >  '
    Write-Host ("[$cid] visible nav: $vis")
  }
}
