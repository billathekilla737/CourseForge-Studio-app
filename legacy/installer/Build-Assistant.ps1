<#
  Build-Assistant.ps1 - build CourseForge Assistant end to end.

    .\Build-Assistant.ps1                       # tests -> python-embed -> PyInstaller -> stage -> smoke -> installer
    .\Build-Assistant.ps1 -SkipPython           # reuse the python-embed\ already prepared
    .\Build-Assistant.ps1 -SkipInstaller        # stop after the smoke test (no Inno Setup)
    .\Build-Assistant.ps1 -Clean                # delete build-assistant\ and dist-assistant\ first
    .\Build-Assistant.ps1 -SignThumbprint <sha1># sign the exe, the setup exe and its uninstaller
    .\Build-Assistant.ps1 -AllowDirty           # local test build from uncommitted source
    .\Build-Assistant.ps1 -RefreshEmbedPins     # re-resolve requirements-embed.txt, then build

  What ends up in dist-assistant\courseforge-assistant\ :
    courseforge-assistant.exe + _internal\    the frozen app (PyInstaller)
    _internal\hooks\cf_assistant_hook.py      the permission hook (plain .py, run
                                              by python\python.exe)
    python\                                   embeddable CPython + the skill's packages
                                              (python-pptx, python-docx, pypdf,
                                              pymupdf, pikepdf, lxml, fontTools)
    skill\                                    the courseforge skill (allow-listed files
                                              only: no credentials, caches or stray notes)
    cf-assistant-icon.ico

  Supply chain, in one place:
    - the Python zip is downloaded over https from python.org and must match the
      SHA-256 pinned below (python.org publishes the MD5; the pin was verified
      against it) or the build stops and the download is deleted
    - packages go in with  pip install --require-hashes --only-binary=:all:
      from requirements-embed.txt (every wheel pinned + hashed; pin_embed.py
      regenerates it). No get-pip, no unpinned "latest", nothing executed from
      a download.
    - source is the CHECKED-IN skill\ in this repo (CF_SCRIPTS overrides), and
      the build refuses uncommitted changes there unless -AllowDirty, so an
      installer can always be traced to a commit
    - the staged skill\ is an allow-list of what ships, not a deny-list of
      what must not
  ASCII only. PowerShell 5.1 compatible.
#>
[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [switch]$Clean,
    [switch]$AllowDirty,
    [switch]$RefreshEmbedPins,
    [string]$PythonVersion = '3.12.10',
    # SHA-256 of python-3.12.10-embed-amd64.zip (MD5 fe8ef205f2e9c3ba44d0cf9954e1abd3 on python.org)
    [string]$PythonSha256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3',
    [string]$SignThumbprint = '',
    [string]$SignCommand = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$here = $PSScriptRoot
Set-Location $here
. (Join-Path $here 'Sign-Common.ps1')

function Step($m) { Write-Host ""; Write-Host ("== {0}" -f $m) -ForegroundColor Cyan }
function Ok($m)   { Write-Host ("   OK  {0}" -f $m) -ForegroundColor Green }
function Die($m)  { Write-Host ("   X   {0}" -f $m) -ForegroundColor Red; exit 1 }

# ---- 0. where is the source? the repo's skill\ unless CF_SCRIPTS says otherwise
$repoRoot = (Resolve-Path (Join-Path $here '..')).Path
$scripts = $env:CF_SCRIPTS
if (-not $scripts) { $scripts = Join-Path $repoRoot 'skill\scripts' }
if (-not (Test-Path (Join-Path $scripts 'courseforge_assistant.py'))) { Die "courseforge_assistant.py not found in $scripts (set CF_SCRIPTS)." }
$scripts = (Resolve-Path $scripts).Path
$skillRoot = (Resolve-Path (Join-Path $scripts '..')).Path
Ok ("source: {0}" -f $scripts)
$inRepo = $skillRoot.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)
if ($inRepo) {
    $head = Assert-CleanSource -RepoRoot $repoRoot -Paths @('skill', 'installer') -AllowDirty:$AllowDirty
    if ($head) { Ok ("building commit {0}" -f $head) }
} elseif (-not $AllowDirty) {
    Die "CF_SCRIPTS points outside this repo; that build cannot be traced to a commit. Pass -AllowDirty for a local test build."
} else {
    Write-Host "   WARNING: building from a folder outside the repo (-AllowDirty)" -ForegroundColor Yellow
}

$signTemplate = Get-SignCommandTemplate -Thumbprint $SignThumbprint -Command $SignCommand -TimestampUrl $TimestampUrl
if ($signTemplate) { Ok "code signing: on" } else { Write-Host "   unsigned build (no -SignThumbprint / -SignCommand)" -ForegroundColor Yellow }

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
    foreach ($t in @('test_courseforge_assistant.py', 'test_restyle_html.py', 'test_courseforge_pdf.py')) {
        $tp = Join-Path $scripts $t
        if (-not (Test-Path $tp)) { continue }
        & python $tp
        if ($LASTEXITCODE -ne 0) { Die "$t failed" }
    }
    Ok "all tests pass"
}

# ---- 2. embeddable python with the skill's packages (verified download, hashed wheels)
if ($RefreshEmbedPins) {
    Step "Re-resolve requirements-embed.txt"
    & python (Join-Path $here 'pin_embed.py')
    if ($LASTEXITCODE -ne 0) { Die "pin_embed.py failed" }
}
& python (Join-Path $here 'pin_embed.py') --check
if ($LASTEXITCODE -ne 0) { Die "requirements-embed.txt is not hash-locked (run pin_embed.py)" }

if (-not $SkipPython) {
    Step ("Embeddable Python {0}" -f $PythonVersion)
    $zip = Join-Path $cache ("python-{0}-embed-amd64.zip" -f $PythonVersion)
    if (-not (Test-Path $zip)) {
        $url = "https://www.python.org/ftp/python/{0}/python-{0}-embed-amd64.zip" -f $PythonVersion
        Write-Host ("   downloading {0}" -f $url)
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    }
    Assert-FileSha256 -File $zip -Expected $PythonSha256 -What "python embed zip"
    Ok "python zip matches its pinned SHA-256"
    if (Test-Path $pyEmbed) { Remove-Item -Recurse -Force $pyEmbed }
    Expand-Archive -Path $zip -DestinationPath $pyEmbed
    # let the embeddable runtime import site-packages (off by default)
    $pth = Get-ChildItem $pyEmbed -Filter 'python*._pth' | Select-Object -First 1
    $lines = Get-Content $pth.FullName | ForEach-Object { if ($_ -eq '#import site') { 'import site' } else { $_ } }
    $lines += 'Lib\site-packages'
    Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII
    # packages are installed BY THE BUILD MACHINE'S pip into the embed's
    # site-packages: no get-pip.py, and pip itself never ships in the app
    $site = Join-Path $pyEmbed 'Lib\site-packages'
    New-Item -ItemType Directory -Force $site | Out-Null
    & python -m pip install --require-hashes --only-binary=:all: `
        --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 `
        --target $site --no-warn-script-location --disable-pip-version-check `
        -r (Join-Path $here 'requirements-embed.txt')
    if ($LASTEXITCODE -ne 0) { Die "hash-verified pip install into the embeddable python failed" }
    Get-ChildItem $pyEmbed -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    $py = Join-Path $pyEmbed 'python.exe'
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
$exe = Join-Path $stage 'courseforge-assistant.exe'
if (-not (Test-Path $exe)) { Die "no exe produced" }
Ok "frozen"

# ---- 4. stage python + skill beside the exe (allow-list)
Step "Stage python\ and skill\"
$stPy = Join-Path $stage 'python'
if (Test-Path $stPy) { Remove-Item -Recurse -Force $stPy }
Copy-Item -Recurse $pyEmbed $stPy
$stSkill = Join-Path $stage 'skill'
if (Test-Path $stSkill) { Remove-Item -Recurse -Force $stSkill }
New-Item -ItemType Directory -Force $stSkill | Out-Null
# exactly what the skill is made of; anything else in the folder (a token
# someone saved as a note, a config, a scratch file) is not shipped
$shipTop   = @('SKILL.md', 'brand.json')
$shipDirs  = @{ 'assets' = @('*.xml'); 'references' = @('*.md'); 'scripts' = @('*.py', '*.ps1') }
$n = 0
foreach ($f in $shipTop) {
    $src = Join-Path $skillRoot $f
    if (-not (Test-Path $src)) { Die "skill file missing: $f" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $stSkill $f) -Force; $n++
}
foreach ($d in $shipDirs.Keys) {
    $srcDir = Join-Path $skillRoot $d
    if (-not (Test-Path $srcDir)) { continue }
    $dstDir = Join-Path $stSkill $d
    New-Item -ItemType Directory -Force $dstDir | Out-Null
    foreach ($pat in $shipDirs[$d]) {
        Get-ChildItem -Path $srcDir -File -Filter $pat | Where-Object { $_.Name -notlike 'test_*' } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dstDir $_.Name) -Force; $n++
        }
    }
}
Copy-Item (Join-Path $here 'cf-assistant-icon.ico') (Join-Path $stage 'cf-assistant-icon.ico') -Force
Ok ("staged skill ({0} files), python, icon" -f $n)

# ---- 5. smoke
Step "Smoke test"
& $exe --version
$p = Start-Process -FilePath $exe -ArgumentList '--smoke' -PassThru -Wait
if ($p.ExitCode -ne 0) { Die ("--smoke exited {0} (see Documents\CourseForge-Assistant\startup-error.txt)" -f $p.ExitCode) }
Ok "window opens and closes"
$hookPy = Join-Path $stage '_internal\hooks\cf_assistant_hook.py'
if (-not (Test-Path $hookPy)) { Die 'cf_assistant_hook.py was not staged' }
$bundledPy = Join-Path $stPy 'python.exe'
# a plain read passes on its own...
$hookReq = '{"tool_name":"Bash","tool_input":{"command":"Get-ChildItem"},"cwd":"C:\\x","session_id":"s","tool_use_id":"t"}'
$hookOut = $hookReq | & $bundledPy $hookPy
if ($hookOut -notmatch '"permissionDecision":\s*"allow"') { Die ("hook under bundled python did not answer allow for a read: {0}" -f $hookOut) }
# ...and a Canvas write must NOT: with no Assistant window to ask, the answer is deny
$hookReq = '{"tool_name":"Bash","tool_input":{"command":"powershell -File Push-CanvasPages.ps1 -Apply"},"cwd":"C:\\x","session_id":"s","tool_use_id":"t"}'
$hookOut = $hookReq | & $bundledPy $hookPy
if ($hookOut -notmatch '"permissionDecision":\s*"deny"') { Die ("hook did not fail closed on a Canvas write: {0}" -f $hookOut) }
Ok "hook runs under the bundled python and fails closed"
$size = (Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Ok ("staged app: {0:N0} MB" -f $size)

# ---- 6. sign the app exe (before it is packaged)
if ($signTemplate) {
    Step "Sign"
    Invoke-Sign -Template $signTemplate -File $exe | Out-Null
    Ok "courseforge-assistant.exe signed"
}

# ---- 7. installer
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
$isccArgs = @((Join-Path $here 'courseforge-assistant.iss')) + (ConvertTo-InnoSignSwitch -Template $signTemplate)
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { Die "Inno Setup failed" }
$setup = Get-ChildItem (Join-Path $here 'Output') -Filter 'CourseForge-Assistant-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$sha = Write-Sha256File -File $setup.FullName
Ok ("installer: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
Ok ("SHA-256: {0}  (also in {1}.sha256 - publish it with the release)" -f $sha, $setup.Name)
if ($signTemplate) {
    $sig = Get-AuthenticodeSignature $setup.FullName
    if ($sig.Status -ne 'Valid') { Die ("setup exe signature is {0}" -f $sig.Status) }
    Ok ("setup exe signed by {0}" -f $sig.SignerCertificate.Subject)
} else {
    Write-Host ""
    Write-Host "   UNSIGNED build. SmartScreen will warn when this is downloaded by a browser." -ForegroundColor Yellow
    Write-Host "   Sign it (-SignThumbprint / -SignCommand) or have IT push it with Intune/SCCM." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Done." -ForegroundColor Green
