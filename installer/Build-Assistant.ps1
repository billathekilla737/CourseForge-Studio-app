<#
  Build-Assistant.ps1 - build CourseForge Assistant end to end.

    .\Build-Assistant.ps1                # tests -> python-embed -> PyInstaller -> stage -> smoke -> installer
    .\Build-Assistant.ps1 -SkipPython    # reuse the python-embed\ already prepared
    .\Build-Assistant.ps1 -SkipInstaller # stop after the smoke test (no Inno Setup)
    .\Build-Assistant.ps1 -Clean         # delete build-assistant\ and dist-assistant\ first

  What ends up in dist-assistant\courseforge-assistant\ :
    courseforge-assistant.exe + _internal\    the frozen app (PyInstaller)
    _internal\hooks\cf_assistant_hook.py      the permission hook (plain .py, run
                                              by python\python.exe)
    python\                                   embeddable CPython + pip + the skill's
                                              packages: python-pptx, python-docx,
                                              pypdf, pymupdf, pikepdf, lxml, fontTools
    skill\                                    the courseforge skill (no credentials,
                                              no __pycache__)
    cf-assistant-icon.ico

  Source is resolved like the PDF Fixer's spec: CF_SCRIPTS, then the live
  skill (%USERPROFILE%\.claude\skills\courseforge\scripts), then ..\skill\scripts.
  ASCII only. PowerShell 5.1 compatible.
#>
[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [switch]$Clean,
    [string]$PythonVersion = '3.12.10'
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$here = $PSScriptRoot
Set-Location $here

function Step($m) { Write-Host ""; Write-Host ("== {0}" -f $m) -ForegroundColor Cyan }
function Ok($m)   { Write-Host ("   OK  {0}" -f $m) -ForegroundColor Green }
function Die($m)  { Write-Host ("   X   {0}" -f $m) -ForegroundColor Red; exit 1 }

# ---- 0. where is the source?
$scripts = $env:CF_SCRIPTS
if (-not $scripts) { $scripts = Join-Path $env:USERPROFILE '.claude\skills\courseforge\scripts' }
foreach ($cand in @($scripts, (Join-Path $here '..\skill\scripts'))) {
    if (Test-Path (Join-Path $cand 'courseforge_assistant.py')) { $scripts = (Resolve-Path $cand).Path; break }
}
if (-not (Test-Path (Join-Path $scripts 'courseforge_assistant.py'))) { Die "courseforge_assistant.py not found (set CF_SCRIPTS)." }
$skillRoot = (Resolve-Path (Join-Path $scripts '..')).Path
Ok ("source: {0}" -f $scripts)

$dist  = Join-Path $here 'dist-assistant'
$work  = Join-Path $here 'build-assistant'
$stage = Join-Path $dist 'courseforge-assistant'
$pyEmbed = Join-Path $here 'python-embed'
$cache = Join-Path $here 'cache'
New-Item -ItemType Directory -Force $cache | Out-Null

if ($Clean) {
    Step "Clean"
    foreach ($d in @($dist, $work)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d; Ok ("removed {0}" -f $d) } }
}

# ---- 1. tests
if (-not $SkipTests) {
    Step "Tests"
    & python (Join-Path $scripts 'test_courseforge_assistant.py')
    if ($LASTEXITCODE -ne 0) { Die "test_courseforge_assistant.py failed" }
    & python (Join-Path $scripts 'test_restyle_html.py')
    if ($LASTEXITCODE -ne 0) { Die "test_restyle_html.py failed" }
    Ok "all tests pass"
}

# ---- 2. embeddable python with the skill's packages
if (-not $SkipPython) {
    Step ("Embeddable Python {0}" -f $PythonVersion)
    $zip = Join-Path $cache ("python-{0}-embed-amd64.zip" -f $PythonVersion)
    if (-not (Test-Path $zip)) {
        $url = "https://www.python.org/ftp/python/{0}/python-{0}-embed-amd64.zip" -f $PythonVersion
        Write-Host ("   downloading {0}" -f $url)
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    }
    if (Test-Path $pyEmbed) { Remove-Item -Recurse -Force $pyEmbed }
    Expand-Archive -Path $zip -DestinationPath $pyEmbed
    # let the embeddable runtime import site-packages (off by default)
    $pth = Get-ChildItem $pyEmbed -Filter 'python*._pth' | Select-Object -First 1
    $lines = Get-Content $pth.FullName | ForEach-Object { if ($_ -eq '#import site') { 'import site' } else { $_ } }
    $lines += 'Lib\site-packages'
    Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII
    $getpip = Join-Path $cache 'get-pip.py'
    if (-not (Test-Path $getpip)) { Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getpip -UseBasicParsing }
    $py = Join-Path $pyEmbed 'python.exe'
    & $py $getpip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { Die "get-pip failed" }
    & $py -m pip install --no-warn-script-location --disable-pip-version-check `
        python-pptx python-docx pypdf pymupdf pikepdf lxml fonttools
    if ($LASTEXITCODE -ne 0) { Die "pip install into the embeddable python failed" }
    # strip what nothing needs at run time
    foreach ($junk in @('Lib\site-packages\pip', 'Lib\site-packages\setuptools', 'Scripts')) {
        $p = Join-Path $pyEmbed $junk
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    Get-ChildItem $pyEmbed -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    & $py -c "import pptx, docx, pypdf, pymupdf, pikepdf, lxml, fontTools; print('embedded python imports OK')"
    if ($LASTEXITCODE -ne 0) { Die "embedded python cannot import the skill's packages" }
    Ok ("python-embed ready ({0:N0} MB)" -f ((Get-ChildItem $pyEmbed -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
} elseif (-not (Test-Path (Join-Path $pyEmbed 'python.exe'))) {
    Die "-SkipPython given but python-embed\ is missing"
}

# ---- 3. freeze
Step "PyInstaller"
$env:CF_SCRIPTS = $scripts
& python -m PyInstaller (Join-Path $here 'courseforge-assistant.spec') --distpath $dist --workpath $work --noconfirm
if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed" }
if (-not (Test-Path (Join-Path $stage 'courseforge-assistant.exe'))) { Die "no exe produced" }
Ok "frozen"

# ---- 4. stage python + skill beside the exe
Step "Stage python\ and skill\"
$stPy = Join-Path $stage 'python'
if (Test-Path $stPy) { Remove-Item -Recurse -Force $stPy }
Copy-Item -Recurse $pyEmbed $stPy
$stSkill = Join-Path $stage 'skill'
if (Test-Path $stSkill) { Remove-Item -Recurse -Force $stSkill }
New-Item -ItemType Directory -Force $stSkill | Out-Null
$excludeDirs  = @('__pycache__', '.git', '.pytest_cache', '.ruff_cache')
$excludeFiles = @('*.pyc', '*.pyo', 'canvas.token', 'canvas.token.enc', '*.token', '*.token.enc',
                  'canvas.config.*.json', 'activity-log.jsonl', '.installed-by-courseforge-assistant.json')
$n = 0
Get-ChildItem -Path $skillRoot -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($skillRoot.Length).TrimStart('\')
    $parts = $rel -split '\\'
    foreach ($d in $excludeDirs) { if ($parts -contains $d) { return } }
    foreach ($pat in $excludeFiles) { if ($_.Name -like $pat) { return } }
    $dest = Join-Path $stSkill $rel
    $destDir = Split-Path -Parent $dest
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force $destDir | Out-Null }
    Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
    $n++
}
Copy-Item (Join-Path $here 'cf-assistant-icon.ico') (Join-Path $stage 'cf-assistant-icon.ico') -Force
Ok ("staged skill ({0} files), python, icon" -f $n)

# ---- 5. smoke
Step "Smoke test"
$exe = Join-Path $stage 'courseforge-assistant.exe'
& $exe --version
$p = Start-Process -FilePath $exe -ArgumentList '--smoke' -PassThru -Wait
if ($p.ExitCode -ne 0) { Die ("--smoke exited {0} (see Documents\CourseForge\startup-error.txt)" -f $p.ExitCode) }
Ok "window opens and closes"
$hookReq = '{"tool_name":"Bash","tool_input":{"command":"Get-ChildItem"},"cwd":"C:\\x","session_id":"s","tool_use_id":"t"}'
$hookPy = Join-Path $stage '_internal\hooks\cf_assistant_hook.py'
if (-not (Test-Path $hookPy)) { $hookPy = Join-Path $stage 'hooks\cf_assistant_hook.py' }
if (-not (Test-Path $hookPy)) { Die 'cf_assistant_hook.py was not staged' }
$hookOut = $hookReq | & (Join-Path $stPy 'python.exe') $hookPy
if ($hookOut -notmatch '"permissionDecision":\s*"allow"') { Die ("hook under bundled python did not answer allow: {0}" -f $hookOut) }
Ok "hook runs under the bundled python"
$size = (Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Ok ("staged app: {0:N0} MB" -f $size)

# ---- 6. installer
if ($SkipInstaller) { Write-Host ""; Write-Host "Done (installer skipped)." -ForegroundColor Green; exit 0 }
Step "Inno Setup"
$iscc = $null
foreach ($cand in @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
                    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                    'C:\Program Files\Inno Setup 6\ISCC.exe')) {
    if (Test-Path $cand) { $iscc = $cand; break }
}
if (-not $iscc) { $c = Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($c) { $iscc = $c.Source } }
if (-not $iscc) { Die "ISCC.exe (Inno Setup 6) not found - install it or re-run with -SkipInstaller" }
& $iscc (Join-Path $here 'courseforge-assistant.iss')
if ($LASTEXITCODE -ne 0) { Die "Inno Setup failed" }
$setup = Get-ChildItem (Join-Path $here 'Output') -Filter 'CourseForge-Assistant-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Ok ("installer: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
Write-Host ""
Write-Host "Done." -ForegroundColor Green
