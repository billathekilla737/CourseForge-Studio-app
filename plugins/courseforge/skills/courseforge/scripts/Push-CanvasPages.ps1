<#
  Push-CanvasPages.ps1
  Creates/updates Canvas pages from styled HTML files listed in a manifest,
  then recreates the weekly Module structure and places each page in order.

  Idempotent: a local state file (canvas.state.json) maps each Notion page id to
  its created Canvas page + module item, so re-running updates in place instead
  of creating duplicates.

  Usage:
    .\Push-CanvasPages.ps1                 # push everything in the manifest
    .\Push-CanvasPages.ps1 -WhatIf         # show what would happen, no writes
#>
param(
    [string]$Root        = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$ConfigPath  = '',   # resolved by CanvasContext.ps1 (cwd, then Documents\canvas-work)
    [string]$ManifestPath= (Join-Path $PSScriptRoot '..\canvas-export\manifest.json'),
    [string]$TokenPath   = '',   # default: canvas.token next to the resolved config
    [string]$CourseId    = '',   # disambiguates when several canvas.config.*.json coexist
    [string]$StatePath   = (Join-Path $PSScriptRoot '..\canvas.state.json'),
    [ValidateSet('published','unpublished')] [string]$PublishState = 'unpublished',
    [switch]$WhatIf
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$ConfigPath = $ctx.ConfigPath; $TokenPath = $ctx.TokenPath

$cfg      = Get-Content -Raw -Encoding UTF8 $ConfigPath   | ConvertFrom-Json
$manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
$token    = (Get-Content -Raw $TokenPath).Trim()
$base     = $cfg.base_url.TrimEnd('/')
$courseId = $cfg.course_id
$api      = "$base/api/v1/courses/$courseId"
$headers  = @{ Authorization = "Bearer $token" }

# Publish state for pages + modules this run touches. Default 'unpublished' keeps
# content hidden from students until the instructor is ready; the skill asks before
# each push and passes -PublishState.
$pub = if ($PublishState -eq 'published') { 'true' } else { 'false' }

# --- state (idempotency) ---------------------------------------------------
function New-State { [pscustomobject]@{ pages=@{}; modules=@{}; items=@{} } }
if (Test-Path $StatePath) {
    $raw = Get-Content -Raw $StatePath | ConvertFrom-Json
    # rehydrate as hashtables we can write to
    $state = New-State
    foreach ($p in $raw.pages.PSObject.Properties)   { $state.pages[$p.Name]   = $p.Value }
    foreach ($m in $raw.modules.PSObject.Properties) { $state.modules[$m.Name] = $m.Value }
    foreach ($i in $raw.items.PSObject.Properties)   { $state.items[$i.Name]   = $i.Value }
} else { $state = New-State }

function Save-State { $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding utf8 }

# Canvas 400s on form-encoded module-item bodies for non-Page types and is
# inconsistent for Page items (Gotcha 3) -> always send module items as JSON
# (UTF-8 bytes). Pages/assignments/discussions still take form bodies.
function Add-ModuleItem {
    param([int]$ModuleId, [hashtable]$Item)
    $json  = (@{ module_item = $Item } | ConvertTo-Json -Compress)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri "$api/modules/$ModuleId/items" -Headers $headers -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -ErrorAction Stop
}

function Invoke-Canvas {
    param([string]$Method, [string]$Path, [hashtable]$Body)
    $uri = if ($Path -match '^https?://') { $Path } else { "$api$Path" }
    try {
        if ($Body) {
            $pairs = foreach ($k in $Body.Keys) { '{0}={1}' -f [uri]::EscapeDataString($k), [uri]::EscapeDataString([string]$Body[$k]) }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
            return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -Body $bytes -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -ErrorAction Stop
        } else {
            return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -ErrorAction Stop
        }
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.Response) {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $msg = "$msg :: " + $sr.ReadToEnd()
        }
        throw "Canvas $Method $uri failed: $msg"
    }
}

# --- ensure a module exists, return its id ---------------------------------
function Get-ModuleId {
    param([string]$Name, [int]$Position)
    if ($state.modules.ContainsKey($Name)) { return $state.modules[$Name] }
    $existing = Invoke-Canvas GET "/modules?per_page=100&search_term=$([uri]::EscapeDataString($Name))"
    $hit = $existing | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if ($hit) { $state.modules[$Name] = $hit.id; return $hit.id }
    if ($WhatIf) { Write-Host "  [WhatIf] would create module '$Name' (pos $Position)"; return -1 }
    $mod = Invoke-Canvas POST "/modules" @{ 'module[name]'=$Name; 'module[position]'=$Position }
    Invoke-Canvas PUT "/modules/$($mod.id)" @{ 'module[published]'=$pub } | Out-Null
    $state.modules[$Name] = $mod.id
    Write-Host "  + module '$Name' (id $($mod.id))"
    return $mod.id
}

# ---------------------------------------------------------------------------
Write-Host "Target: $($cfg.course_label)  ($api)"
Write-Host "Manifest: $($manifest.course_label) - $($manifest.pages.Count) page(s)"
Write-Host "Publish state: $PublishState"
Write-Host ""

$created=0; $updated=0; $skipped=0

foreach ($p in $manifest.pages) {
    $full = Join-Path $Root $p.file
    if (-not (Test-Path $full)) { Write-Host "  ! skip (no html yet): $($p.title)"; $skipped++; continue }
    $html = Get-Content -Raw -Encoding UTF8 $full

    $known = $state.pages[$p.notion_id]
    if ($WhatIf) {
        $verb = if ($known) { 'update' } else { 'create' }
        Write-Host "  [WhatIf] would $verb page '$($p.title)' -> module '$($p.module)'"
        continue
    }

    if ($known -and $known.url) {
        $resp = Invoke-Canvas PUT "/pages/$($known.url)" @{ 'wiki_page[title]'=$p.title; 'wiki_page[body]'=$html; 'wiki_page[published]'=$pub }
        # Editing a page title regenerates its slug (Gotcha 4) -> trust the
        # response url, not the stored one, before we add it to a module.
        $pageUrl = if ($resp.url)     { $resp.url }     else { $known.url }
        $pageId  = if ($resp.page_id) { $resp.page_id } else { $known.page_id }
        $state.pages[$p.notion_id] = [pscustomobject]@{ url=$pageUrl; page_id=$pageId; title=$p.title }
        Write-Host "  ~ updated page '$($p.title)'  (/$pageUrl)"
        $updated++
    } else {
        $page = Invoke-Canvas POST "/pages" @{ 'wiki_page[title]'=$p.title; 'wiki_page[body]'=$html; 'wiki_page[published]'=$pub }
        $pageUrl = $page.url; $pageId = $page.page_id
        $state.pages[$p.notion_id] = [pscustomobject]@{ url=$pageUrl; page_id=$pageId; title=$p.title }
        Write-Host "  + created page '$($p.title)'  (/$pageUrl)"
        $created++
    }

    # place in module
    $mid = Get-ModuleId -Name $p.module -Position $p.module_position
    $itemKey = "${mid}::${pageUrl}"
    if (-not $state.items.ContainsKey($itemKey)) {
        $item = Add-ModuleItem -ModuleId $mid -Item @{ type='Page'; page_url=$pageUrl; title=$p.title; position=$p.position }
        $state.items[$itemKey] = $item.id
        Write-Host "    -> added to module '$($p.module)' (pos $($p.position))"
    }
    Save-State
}

Write-Host ""
Write-Host "Done. created=$created updated=$updated skipped=$skipped"
if (-not $WhatIf) { Write-Host ("Review: {0}/courses/{1}/modules" -f $base, $courseId) }
