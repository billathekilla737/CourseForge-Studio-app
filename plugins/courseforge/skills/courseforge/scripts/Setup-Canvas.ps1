<#
  Setup-Canvas.ps1  (courseforge - one-command onboarding for non-technical instructors)

  Goal: the instructor never touches a file, a folder path, or a file extension.
  They answer two plain questions and the script does the rest:
     1. Their Canvas course web address (the URL in the browser).
     2. Their access token (typed HIDDEN, like a password box).

  It then:
     - works out the school URL + course id from the web address,
     - saves the token correctly as  canvas.token  (clean, no stray .txt, no BOM),
     - writes  canvas.config.<courseId>.json,
     - drops a .gitignore so the token can never be pushed to GitHub by accident,
     - and immediately TESTS the token against Canvas, printing the course name.

  Forgiving by design: if the instructor already dropped the token into a file
  (canvas.token.txt, "Canvas Token.txt", a pasted .rtf, any *.token), the script
  finds it, fixes it, and reuses it instead of asking again.

  Normal use (interactive - this is what an instructor runs):
     .\Setup-Canvas.ps1

  Automation / testing (no prompts):
     .\Setup-Canvas.ps1 -CourseUrl https://school.instructure.com/courses/12345 -Token 1234~abcd...
#>
[CmdletBinding()]
param(
    [string]$WorkingDir,            # where to save things; default = current folder
    [string]$CourseUrl,             # if omitted, the script asks
    [string]$Token,                 # if omitted, the script asks (hidden) or reuses a stray file
    [string]$CourseLabel,           # optional friendly name; default = the real Canvas course name
    [switch]$ShowToken              # type the token visibly instead of hidden (not recommended)
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step($n, $msg) { Write-Host ""; Write-Host ("[{0}] {1}" -f $n, $msg) -ForegroundColor Cyan }
function Write-Good($msg)     { Write-Host ("    OK  {0}" -f $msg) -ForegroundColor Green }
function Write-Warn2($msg)    { Write-Host ("    !   {0}" -f $msg) -ForegroundColor Yellow }
function Write-Bad($msg)      { Write-Host ("    X   {0}" -f $msg) -ForegroundColor Red }

$TokenShape = '^\d{2,6}~[A-Za-z0-9]{40,90}$'

Write-Host ""
Write-Host "=====================================================" -ForegroundColor White
Write-Host "  CourseForge - Canvas setup" -ForegroundColor White
Write-Host "  Two quick questions and you are done." -ForegroundColor Gray
Write-Host "=====================================================" -ForegroundColor White

# --- 0. Where are we saving? ------------------------------------------------------
if (-not $WorkingDir) { $WorkingDir = (Get-Location).Path }
if (-not (Test-Path $WorkingDir)) { New-Item -ItemType Directory -Force $WorkingDir | Out-Null }
$WorkingDir = (Resolve-Path $WorkingDir).Path

# --- 1. Course web address --------------------------------------------------------
Write-Step 1 "Your Canvas course web address"
function Parse-CourseUrl([string]$url) {
    if ($url -match '^\s*https?://([^/]+).*?/courses/(\d+)') {
        return [pscustomobject]@{ BaseUrl = ("https://{0}" -f $matches[1].TrimEnd('/')); CourseId = $matches[2] }
    }
    return $null
}
$parsed = $null
if ($CourseUrl) { $parsed = Parse-CourseUrl $CourseUrl }
while (-not $parsed) {
    Write-Host "    Open your course in Canvas and copy the address bar. It looks like:"
    Write-Host "      https://yourschool.instructure.com/courses/12345" -ForegroundColor Gray
    $entry = Read-Host "    Paste your Canvas course web address"
    $parsed = Parse-CourseUrl $entry
    if (-not $parsed) { Write-Bad "That did not look like a Canvas course address. It must contain '/courses/<number>'. Try again." }
}
$baseUrl  = $parsed.BaseUrl
$courseId = $parsed.CourseId
Write-Good ("School: {0}" -f $baseUrl)
Write-Good ("Course id: {0}" -f $courseId)

# --- 2. Token: reuse a stray file, use -Token, or ask (hidden) --------------------
Write-Step 2 "Your Canvas access token"

function Read-StrayToken([string]$dir) {
    # Look for a token the instructor may have already saved, in likely forms.
    $candidates = @()
    $candidates += Get-ChildItem -Path $dir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '(?i)^canvas\.token(\.txt)?$' -or
                       $_.Name -match '(?i)token' -and $_.Extension -in '.txt','.rtf','.token' -or
                       $_.Extension -eq '.token' }
    foreach ($f in $candidates) {
        $raw = Get-Content -Raw -Path $f.FullName -ErrorAction SilentlyContinue
        if (-not $raw) { continue }
        # If it is an .rtf, strip the formatting to find the token (RTF splits it up).
        if ($f.Extension -eq '.rtf' -or $raw -match '^\s*{\\rtf') {
            $cut = $raw.IndexOf('{\*\themedata'); if ($cut -gt 0) { $raw = $raw.Substring(0, $cut) }
            $raw = $raw -replace '\\\*',' ' -replace '\\[a-zA-Z]+-?\d* ?',' ' -replace '[{}]',' '
            $raw = ($raw -replace '\s','')
        }
        $cand = $raw.Trim()
        if ($cand -match $TokenShape) {
            return [pscustomobject]@{ Token = $cand; Source = $f.FullName }
        }
        if ($raw -match '(\d{2,6}~[A-Za-z0-9]{40,90})') {
            return [pscustomobject]@{ Token = $matches[1]; Source = $f.FullName }
        }
    }
    return $null
}

$tokenValue  = $null
$tokenSource = $null

if ($Token) {
    if ($Token.Trim() -match $TokenShape) { $tokenValue = $Token.Trim(); $tokenSource = '(provided)' }
    else { Write-Bad "The -Token value is not shaped like a Canvas token (NN~xxxx...)."; exit 1 }
}

if (-not $tokenValue) {
    $stray = Read-StrayToken $WorkingDir
    if ($stray) {
        $tokenValue  = $stray.Token
        $tokenSource = $stray.Source
        Write-Good ("Found a token you already saved here - reusing it:")
        Write-Host  ("        {0}" -f $stray.Source) -ForegroundColor Gray
    }
}

if (-not $tokenValue) {
    Write-Host "    In Canvas: Account -> Settings -> '+ New Access Token' -> Generate."
    Write-Host "    Copy the long code it shows you (it starts with numbers then a ~)."
    Write-Host ""
    while (-not $tokenValue) {
        if ($ShowToken) {
            $entry = Read-Host "    Paste your access token"
        } else {
            $secure = Read-Host "    Paste your access token (it will stay hidden)" -AsSecureString
            $bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $entry  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $entry = ($entry | Out-String).Trim()
        if ($entry -match $TokenShape) { $tokenValue = $entry }
        elseif ($entry -match '(\d{2,6}~[A-Za-z0-9]{40,90})') { $tokenValue = $matches[1] }
        else { Write-Bad "That does not look like a Canvas token (should be like 1234~AbC...). Try again." }
    }
}

$mask = $tokenValue.Substring(0, [Math]::Min(5, $tokenValue.Length))
Write-Good ("Token captured  (starts {0}...  length {1})" -f $mask, $tokenValue.Length)

# --- 3. Save the files (correctly) -----------------------------------------------
Write-Step 3 "Saving your settings"
$tokenPath  = Join-Path $WorkingDir 'canvas.token'
$configPath = Join-Path $WorkingDir ("canvas.config.{0}.json" -f $courseId)

# token: plain ASCII, single line, no trailing newline, no BOM
[IO.File]::WriteAllText($tokenPath, $tokenValue, (New-Object System.Text.ASCIIEncoding))
Write-Good ("Saved token -> {0}" -f $tokenPath)

# clean up a stray .txt/.rtf token file so it is not left lying around
if ($tokenSource -and ($tokenSource -ne $tokenPath) -and (Test-Path $tokenSource)) {
    try { Remove-Item -Force $tokenSource; Write-Good ("Tidied up the old token file: {0}" -f (Split-Path $tokenSource -Leaf)) } catch {}
}

# --- 4. Test it against Canvas ----------------------------------------------------
Write-Step 4 "Testing the connection to Canvas"
$headers = @{ Authorization = ("Bearer {0}" -f $tokenValue) }
$course  = $null
try {
    $course = Invoke-RestMethod -Uri ("{0}/api/v1/courses/{1}?include[]=total_students" -f $baseUrl, $courseId) -Headers $headers -Method GET -ErrorAction Stop
} catch {
    $code = $null
    try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
    Write-Bad "Could not connect to Canvas with that token."
    if ($code -eq 401) {
        Write-Warn2 "Canvas said 'unauthorized' (401). The token is wrong, expired, or was revoked."
        Write-Warn2 "Generate a fresh token in Canvas (Account -> Settings) and run setup again."
    } elseif ($code -eq 404) {
        Write-Warn2 "Canvas said 'not found' (404). Double-check the course web address you pasted."
    } else {
        Write-Warn2 ("Details: {0}" -f $_.Exception.Message)
    }
    Write-Host ""
    Write-Warn2 "Your token was saved, but it did not work yet. Fix the above and re-run:  .\Setup-Canvas.ps1"
    exit 2
}

if (-not $CourseLabel) { $CourseLabel = $course.name }
$config = [ordered]@{ base_url = $baseUrl; course_id = "$courseId"; course_label = $CourseLabel }
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
Write-Good ("Saved course settings -> {0}" -f $configPath)

# --- 5. Protect the token from being pushed --------------------------------------
$giPath = Join-Path $WorkingDir '.gitignore'
$giLines = @(
    '# Added by CourseForge setup - never commit credentials or student data',
    'canvas.token','*.token','canvas.config.*.json','canvas.state.*.json',
    'canvas.project.*.json','canvas-admin-audit.log','canvas-export/',
    'grading/','private/','**/map.json','**/proposed-grades*.json'
)
$existing = ''
if (Test-Path $giPath) { $existing = Get-Content -Raw $giPath }
$append = @()
foreach ($l in $giLines) { if ($existing -notmatch [regex]::Escape($l)) { $append += $l } }
if ($append.Count -gt 0) {
    $prefix = ''
    if ($existing -and -not $existing.EndsWith("`n")) { $prefix = "`r`n" }
    Add-Content -Path $giPath -Value ($prefix + ($append -join "`r`n"))
}

# --- Done -------------------------------------------------------------------------
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  All set! Canvas is connected." -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ("  Course:         {0}" -f $course.name)
Write-Host ("  Status:         {0}" -f $course.workflow_state)
Write-Host ("  Students:       {0}" -f $course.total_students)
Write-Host ("  Saved in:       {0}" -f $WorkingDir)
Write-Host ""
Write-Host "  You can now ask Claude to build or update this course." -ForegroundColor White
Write-Host "  Keep canvas.token private; if it ever leaks, revoke it in Canvas (Account -> Settings)." -ForegroundColor Gray
Write-Host ""
