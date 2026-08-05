<#
  lib_canvas.ps1 - shared Canvas LMS REST helpers. Dot-source this from any script:
      . (Join-Path $PSScriptRoot 'lib_canvas.ps1')

  Cross-platform: Windows PowerShell 5.1 and PowerShell 7+ (macOS/Linux). No external
  modules required - only Invoke-RestMethod / Invoke-WebRequest from the box.

  Config resolution order (first match wins):
    1. Explicit -ConfigPath / -TokenPath parameters passed to Resolve-CanvasConfig.
    2. canvas.config.<id>.json (+ canvas.token beside it) in the current directory.
    3. The same, in ~/Documents/canvas-work (the CourseForge convention) - only used
       if there is exactly one canvas.config.*.json there; ambiguity is a hard error.

  canvas.config.<id>.json shape:  { "base_url": "https://school.instructure.com",
                                     "course_id": "12345", "course_label": "..." }
  canvas.token: a single line containing the API token. Never commit this file.
#>

function Resolve-CanvasConfig {
    param(
        [string]$ConfigPath,
        [string]$TokenPath,
        [string]$CourseId
    )
    if (-not $ConfigPath) {
        $searchDirs = @((Get-Location).Path, (Join-Path $HOME 'Documents/canvas-work'))
        foreach ($dir in $searchDirs) {
            if (-not (Test-Path $dir)) { continue }
            $pattern = if ($CourseId) { "canvas.config.$CourseId.json" } else { 'canvas.config.*.json' }
            $matches = @(Get-ChildItem -Path $dir -Filter $pattern -File -ErrorAction SilentlyContinue)
            if ($matches.Count -eq 1) { $ConfigPath = $matches[0].FullName; break }
            elseif ($matches.Count -gt 1) {
                throw "Multiple canvas.config.*.json found in $dir - pass -CourseId or -ConfigPath to disambiguate."
            }
        }
        if (-not $ConfigPath) { throw "No canvas.config.*.json found. Pass -ConfigPath, or run from a folder that has one." }
    }
    if (-not (Test-Path $ConfigPath)) { throw "Canvas config not found: $ConfigPath" }
    $cfg = Get-Content -Raw $ConfigPath | ConvertFrom-Json

    if (-not $TokenPath) { $TokenPath = Join-Path (Split-Path -Parent $ConfigPath) 'canvas.token' }
    if (-not (Test-Path $TokenPath)) { throw "Canvas token not found: $TokenPath" }
    $token = (Get-Content -Raw $TokenPath).Trim()

    return [pscustomobject]@{
        ConfigPath = $ConfigPath
        TokenPath  = $TokenPath
        BaseUrl    = $cfg.base_url.TrimEnd('/')
        CourseId   = "$($cfg.course_id)"
        Label      = $cfg.course_label
        Headers    = @{ Authorization = "Bearer $token" }
    }
}

function Invoke-CanvasGet {
    <# GET a single page. Use Get-CanvasPaged for list endpoints. #>
    param([Parameter(Mandatory)][string]$Url, [Parameter(Mandatory)][hashtable]$Headers)
    return Invoke-RestMethod -Uri $Url -Headers $Headers -Method Get
}

function Get-CanvasPaged {
    <# Follows Canvas's Link header pagination and returns the full flattened list. #>
    param([Parameter(Mandatory)][string]$Url, [Parameter(Mandatory)][hashtable]$Headers)
    $out = New-Object System.Collections.ArrayList
    $next = $Url
    while ($next) {
        $r = Invoke-WebRequest -Uri $next -Headers $Headers -UseBasicParsing -Method Get
        $page = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
        foreach ($item in @($page)) { [void]$out.Add($item) }
        $next = $null
        if ($r.Headers.Link) {
            foreach ($part in ($r.Headers.Link -split ',')) {
                if ($part -match '<([^>]+)>;\s*rel="next"') { $next = $Matches[1] }
            }
        }
    }
    return @($out)
}

function Send-CanvasJson {
    <#
      PUT/POST/DELETE with a UTF-8 JSON body. Canvas wants request bodies as raw UTF-8
      bytes for anything containing non-ASCII or entity-heavy HTML; Invoke-RestMethod's
      default -Body string path can mangle encoding on some PowerShell/.NET versions,
      so this always converts explicitly.
    #>
    param(
        [Parameter(Mandatory)][ValidateSet('Get', 'Post', 'Put', 'Delete')][string]$Method,
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][hashtable]$Headers,
        $Body = $null,
        [int]$Depth = 14
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Url -Headers $Headers
    }
    $json = $Body | ConvertTo-Json -Depth $Depth -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Method $Method -Uri $Url -Headers $Headers `
        -ContentType 'application/json; charset=utf-8' -Body $bytes
}

function Read-Utf8File {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { throw "File not found: $Path" }
    return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
}
