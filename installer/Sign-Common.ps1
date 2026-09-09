<#
  Sign-Common.ps1 - shared build helpers, dot-sourced by Build-Assistant.ps1
  and Build-PdfFixer.ps1. ASCII only. PowerShell 5.1 compatible.

    Resolve-SignTool           newest signtool.exe from the Windows SDK, or PATH
    Get-SignCommandTemplate    "signtool sign ... $f" for Inno's /S switch, or ''
    Invoke-Sign <file>         sign one file in place (no-op when unsigned build)
    Assert-CleanSource         refuse to build from a checkout with uncommitted
                               changes to the shipped source (unless -AllowDirty)
    Write-Sha256File <file>    <file>.sha256 next to the artifact, for the release
    Assert-FileSha256          die unless a downloaded file matches its pin

  Signing is configured by ONE of:
    -SignThumbprint <sha1>     a code-signing certificate in the current user's
                               or machine's certificate store (internal CA or a
                               purchased certificate on a hardware token)
    -SignCommand '<template>'  any full command with $f standing for the file,
                               e.g. Azure Trusted Signing:
                               'signtool sign /v /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "...\Azure.CodeSigning.Dlib.dll" /dmdf "...\metadata.json" $f'
  Neither given = an unsigned build, reported loudly at the end.
#>

function Resolve-SignTool {
    $c = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $kits = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "${env:ProgramFiles}\Windows Kits\10\bin")
    foreach ($k in $kits) {
        if (-not (Test-Path $k)) { continue }
        $hit = Get-ChildItem $k -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($hit) { return $hit }
    }
    return $null
}

function Get-SignCommandTemplate {
    param([string]$Thumbprint, [string]$Command, [string]$TimestampUrl)
    if ($Command) { return $Command }
    if (-not $Thumbprint) { return '' }
    $st = Resolve-SignTool
    if (-not $st) { throw "signtool.exe not found: install the Windows SDK signing tools or pass -SignCommand" }
    return ('"{0}" sign /fd SHA256 /td SHA256 /tr {1} /sha1 {2} $f' -f $st, $TimestampUrl, $Thumbprint)
}

function Invoke-Sign {
    param([Parameter(Mandatory)][string]$Template, [Parameter(Mandatory)][string]$File)
    if (-not $Template) { return $false }
    $cmd = $Template.Replace('$f', ('"{0}"' -f $File))
    # cmd.exe runs the template as written so /dlib-style commands work unchanged
    & cmd.exe /c $cmd
    if ($LASTEXITCODE -ne 0) { throw ("signing failed for {0}" -f $File) }
    $sig = Get-AuthenticodeSignature $File
    if ($sig.Status -ne 'Valid') { throw ("{0} is not validly signed after signing: {1}" -f $File, $sig.Status) }
    return $true
}

function ConvertTo-InnoSignSwitch {
    # Inno's /S switch takes  name="command $f" ; inner quotes must become $q
    param([string]$Template)
    if (-not $Template) { return @() }
    $inner = $Template.Replace('"', '$q')
    return @('/DSign', ('/Scfsign={0}' -f $inner))
}

function Assert-CleanSource {
    param([Parameter(Mandatory)][string]$RepoRoot, [string[]]$Paths, [switch]$AllowDirty)
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        if ($AllowDirty) { Write-Host "   (git not found; -AllowDirty given, skipping the clean-source check)" -ForegroundColor Yellow; return }
        throw "git is required to prove the build matches a commit (or pass -AllowDirty)"
    }
    Push-Location $RepoRoot
    try {
        $dirty = & git status --porcelain -- @Paths
        $head  = (& git rev-parse --short HEAD).Trim()
    } finally { Pop-Location }
    if ($dirty) {
        Write-Host "   uncommitted changes in the shipped source:" -ForegroundColor Yellow
        $dirty | ForEach-Object { Write-Host ("     {0}" -f $_) -ForegroundColor Yellow }
        if (-not $AllowDirty) { throw "commit first, so the installer can be traced to a commit (or pass -AllowDirty for a local test build)" }
    }
    return $head
}

function Write-Sha256File {
    param([Parameter(Mandatory)][string]$File)
    $h = (Get-FileHash -Algorithm SHA256 $File).Hash.ToLower()
    $out = "$File.sha256"
    Set-Content -Path $out -Value ("{0} *{1}" -f $h, (Split-Path -Leaf $File)) -Encoding ASCII
    return $h
}

function Assert-FileSha256 {
    param([Parameter(Mandatory)][string]$File, [Parameter(Mandatory)][string]$Expected, [string]$What = 'file')
    $got = (Get-FileHash -Algorithm SHA256 $File).Hash.ToLower()
    if ($got -ne $Expected.ToLower()) {
        Remove-Item -Force $File -ErrorAction SilentlyContinue
        throw ("{0} does not match its pinned SHA-256 (got {1}); the download was deleted" -f $What, $got)
    }
}
