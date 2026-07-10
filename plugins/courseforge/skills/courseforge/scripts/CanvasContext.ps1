<#
  CanvasContext.ps1 (courseforge) - shared course-context resolver. Dot-source this.

  ONE convention for every script (replaces four divergent per-script defaults):
    config : -ConfigPath if given; else canvas.config.<CourseId>.json when -CourseId
             is given; else the SINGLE canvas.config.*.json found in (1) the current
             directory, then (2) Documents\canvas-work.
             Multiple configs + no -CourseId  ->  hard error listing them. A designer
             working many courses must say which; nothing is ever picked silently.
    token  : -TokenPath if given; else canvas.token NEXT TO the chosen config.
             Designers keep one folder per course/instructor (config + token side
             by side); a single instructor's canvas-work folder still just works.

  Returns @{ ConfigPath; TokenPath; Config }  (Config = parsed JSON object).
  ASCII only. PowerShell 5.1 compatible. Throws on any ambiguity or missing file.
#>
function Resolve-CanvasContext {
    param(
        [string]$ConfigPath,
        [string]$TokenPath,
        [string]$CourseId
    )
    if (-not $ConfigPath) {
        $dirs = @((Get-Location).Path, (Join-Path $env:USERPROFILE 'Documents\canvas-work'))
        foreach ($d in $dirs) {
            if (-not (Test-Path $d)) { continue }
            $pattern = if ($CourseId) { "canvas.config.$CourseId.json" } else { 'canvas.config.*.json' }
            $found = @(Get-ChildItem -Path $d -Filter $pattern -File -ErrorAction SilentlyContinue)
            if ($found.Count -eq 1) { $ConfigPath = $found[0].FullName; break }
            if ($found.Count -gt 1) {
                $names = ($found | ForEach-Object { $_.Name }) -join ', '
                throw ("Multiple Canvas configs in {0}: {1}. Pass -CourseId <id> or -ConfigPath to choose." -f $d, $names)
            }
        }
        if (-not $ConfigPath) {
            $forCourse = ''
            if ($CourseId) { $forCourse = " for course $CourseId" }
            throw ("No canvas.config.*.json found in the current directory or Documents\canvas-work{0}. Run Setup-Canvas.ps1 first, or pass -ConfigPath." -f $forCourse)
        }
    }
    if (-not (Test-Path $ConfigPath)) { throw "Canvas config not found: $ConfigPath" }
    $ConfigPath = (Resolve-Path $ConfigPath).Path
    if (-not $TokenPath) {
        $TokenPath = Join-Path (Split-Path -Parent $ConfigPath) 'canvas.token'
    }
    if (-not (Test-Path $TokenPath)) {
        throw ("canvas.token not found next to the config ({0}). Pass -TokenPath explicitly." -f $TokenPath)
    }
    $cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
    return @{ ConfigPath = $ConfigPath; TokenPath = (Resolve-Path $TokenPath).Path; Config = $cfg }
}
