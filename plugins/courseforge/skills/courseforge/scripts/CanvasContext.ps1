<#
  CanvasContext.ps1 (courseforge) - shared course-context resolver. Dot-source this.

  ONE convention for every script (replaces four divergent per-script defaults):
    config : -ConfigPath if given; else canvas.config.<CourseId>.json when -CourseId
             is given; else the SINGLE canvas.config.*.json found in (1) the current
             directory, then (2) Documents\canvas-work -- BOTH $env:USERPROFILE\Documents
             and the shell's redirected Documents (OneDrive Known Folder Move), which on a
             redirected machine are two different folders.
             Multiple configs + no -CourseId  ->  hard error listing them. A designer
             working many courses must say which; nothing is ever picked silently.
    token  : -TokenPath if given; else canvas.token NEXT TO the chosen config.
             Designers keep one folder per course/instructor (config + token side
             by side); a single instructor's canvas-work folder still just works.

  Returns @{ ConfigPath; TokenPath; Config }  (Config = parsed JSON object).
  ASCII only. PowerShell 5.1 compatible. Throws on any ambiguity or missing file.
#>
. "$PSScriptRoot\CanvasToken.ps1"

function Test-CanvasThrottle {
    <# Canvas answers its RATE LIMITER with 403 - the same status it uses for
       "you may not touch this course". Only the throttle is worth retrying,
       and only the throttle should ever be reported as one. #>
    param($ErrorRecord)
    try {
        $resp = $ErrorRecord.Exception.Response
        if (-not $resp) { return $false }
        $code = [int]$resp.StatusCode
        if ($code -eq 429) { return $true }
        if ($code -ne 403) { return $false }
        if ($resp.Headers -and $resp.Headers['X-Rate-Limit-Remaining']) { return $true }
        $sr = New-Object IO.StreamReader($resp.GetResponseStream())
        $body = $sr.ReadToEnd(); $sr.Close()
        return ($body -match '(?i)rate\s*limit|throttl')
    } catch { return $false }
}

function Invoke-CanvasApi {
    <# One Canvas request with a timeout and backoff on throttling / 5xx.
       Scripts used to call Invoke-RestMethod bare: no timeout (a hung
       connection hangs the designer's console indefinitely) and no retry, so
       one throttle part-way through a 200-item course failed the whole run. #>
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][hashtable]$Headers,
        $Body,
        [string]$ContentType,
        [int]$TimeoutSec = 120,
        [int]$MaxAttempts = 5
    )
    $attempt = 0
    while ($true) {
        $attempt++
        try {
            $call = @{ Method = $Method; Uri = $Uri; Headers = $Headers
                       TimeoutSec = $TimeoutSec; ErrorAction = 'Stop' }
            if ($PSBoundParameters.ContainsKey('Body') -and $null -ne $Body) { $call.Body = $Body }
            if ($ContentType) { $call.ContentType = $ContentType }
            return Invoke-RestMethod @call
        } catch {
            $code = 0
            try { $code = [int]$_.Exception.Response.StatusCode } catch {}
            $throttled = Test-CanvasThrottle $_
            $retryable = $throttled -or ($code -ge 500 -and $code -le 599) -or ($code -eq 0)
            if (-not $retryable -or $attempt -ge $MaxAttempts) { throw }
            $wait = [Math]::Min(20, [Math]::Pow(2, $attempt - 1)) + (Get-Random -Minimum 0.0 -Maximum 1.0)
            if ($throttled) {
                Write-Host ("  Canvas is rate limiting; waiting {0:N1}s (attempt {1}/{2})" -f $wait, $attempt, $MaxAttempts)
            } else {
                Write-Host ("  Canvas returned {0}; retrying in {1:N1}s (attempt {2}/{3})" -f $code, $wait, $attempt, $MaxAttempts)
            }
            Start-Sleep -Seconds $wait
        }
    }
}

function Get-CanvasPaged {
    # Follow Link rel="next" and return ALL items. Assign-then-return @($var)
    # (PS 5.1: @(<cmdlet>) nests a JSON array and .Count lies).
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][hashtable]$Headers,
        [int]$TimeoutSec = 120,
        [int]$MaxPages = 500
    )
    $out = @()
    $seen = @{}
    $pages = 0
    while ($Url) {
        # a next link that does not advance would otherwise spin forever
        if ($seen.ContainsKey($Url)) { break }
        $seen[$Url] = $true
        $pages++
        if ($pages -gt $MaxPages) {
            Write-Warning ("Stopped paginating after {0} pages - Canvas kept offering a next link." -f $MaxPages)
            break
        }
        $resp = $null
        $attempt = 0
        while ($true) {
            $attempt++
            try {
                $resp = Invoke-WebRequest -Uri $Url -Headers $Headers -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
                break
            } catch {
                $code = 0
                try { $code = [int]$_.Exception.Response.StatusCode } catch {}
                $throttled = Test-CanvasThrottle $_
                if ((-not ($throttled -or ($code -ge 500 -and $code -le 599) -or $code -eq 0)) -or $attempt -ge 5) { throw }
                $wait = [Math]::Min(20, [Math]::Pow(2, $attempt - 1)) + (Get-Random -Minimum 0.0 -Maximum 1.0)
                Write-Host ("  Canvas returned {0} while paging; retrying in {1:N1}s" -f $code, $wait)
                Start-Sleep -Seconds $wait
            }
        }
        $page = ([Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())) | ConvertFrom-Json
        $out += @($page)
        $Url = $null
        if ($resp.Headers.Link) {
            foreach ($part in ($resp.Headers.Link -split ',')) {
                if ($part -match '<([^>]+)>;\s*rel="next"') { $Url = $Matches[1] }
            }
        }
    }
    return @($out)
}

function Resolve-CanvasContext {
    param(
        [string]$ConfigPath,
        [string]$TokenPath,
        [string]$CourseId
    )
    if (-not $ConfigPath) {
        # Documents\canvas-work is checked TWICE on purpose. On a machine where OneDrive
        # (or any Known Folder redirection) has moved Documents, $env:USERPROFILE\Documents
        # is a near-empty legacy folder while the Documents the instructor actually sees in
        # File Explorer is somewhere else entirely (e.g. ...\OneDrive\Documents). Checking
        # only the first produced a "No canvas.config.*.json found" error while the folder
        # was plainly sitting right there. GetFolderPath('MyDocuments') honors redirection.
        $dirs = @((Get-Location).Path, (Join-Path $env:USERPROFILE 'Documents\canvas-work'))
        $shellDocs = ''
        try { $shellDocs = [Environment]::GetFolderPath('MyDocuments') } catch {}
        if ($shellDocs) {
            $redirected = Join-Path $shellDocs 'canvas-work'
            if ($dirs -notcontains $redirected) { $dirs += $redirected }
        }
        $foundIn = ''
        foreach ($d in $dirs) {
            if (-not (Test-Path $d)) { continue }
            $pattern = if ($CourseId) { "canvas.config.$CourseId.json" } else { 'canvas.config.*.json' }
            $found = @(Get-ChildItem -Path $d -Filter $pattern -File -ErrorAction SilentlyContinue)
            if ($found.Count -eq 1) { $ConfigPath = $found[0].FullName; $foundIn = $d; break }
            if ($found.Count -gt 1) {
                $names = ($found | ForEach-Object { $_.Name }) -join ', '
                throw ("Multiple Canvas configs in {0}: {1}. Pass -CourseId <id> or -ConfigPath to choose." -f $d, $names)
            }
        }
        if (-not $ConfigPath) {
            $forCourse = ''
            if ($CourseId) { $forCourse = " for course $CourseId" }
            throw ("No canvas.config.*.json found{0}. Looked in: {1}. Run Setup-Canvas.ps1 first, or pass -ConfigPath." -f $forCourse, ($dirs -join '; '))
        }
        # Never resolve a course SILENTLY. Multiple configs already hard-error, but a
        # single config in a FALLBACK directory used to be picked with no announcement:
        # run any script from a folder that has no config of its own and it quietly
        # targets whatever course is configured in Documents\canvas-work - easily a
        # stale sandbox from a previous term. Announce the fallback so a wrong target is
        # visible in the transcript instead of being discovered after the write.
        if ($foundIn -and $foundIn -ne (Get-Location).Path) {
            Write-Host ("  CanvasContext: using {0} from FALLBACK folder {1} - not the current directory. Pass -CourseId or -ConfigPath to be explicit." -f (Split-Path -Leaf $ConfigPath), $foundIn)
        }
    }
    if (-not (Test-Path $ConfigPath)) { throw "Canvas config not found: $ConfigPath" }
    $ConfigPath = (Resolve-Path $ConfigPath).Path
    # The token is DECRYPTED HERE and handed back in memory as .Token. Callers
    # must use $ctx.Token and never read the file themselves - that is what
    # keeps the on-disk form encrypted and swappable in one place.
    $tok = Get-CanvasToken -TokenPath $TokenPath -Dir (Split-Path -Parent $ConfigPath)
    $cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
    return @{ ConfigPath = $ConfigPath
              TokenPath  = $tok.Path
              Token      = $tok.Token
              Encrypted  = $tok.Encrypted
              Config     = $cfg }
}

function Get-CanvasHeaders {
    <# Standard auth header from a resolved context. #>
    param([Parameter(Mandatory)]$Context)
    return @{ Authorization = ("Bearer {0}" -f $Context.Token) }
}
