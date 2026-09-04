<#
  CanvasToken.ps1 (courseforge) - encrypted Canvas token storage. Dot-sourced
  by CanvasContext.ps1; nothing else needs to know how this works.

  WHY: the token used to live in a plaintext canvas.token next to the config.
  It is a bearer credential for a whole Canvas account - it can read every
  course the instructor can see, and student data with it - and a plaintext
  copy sitting in a working folder gets emailed, zipped, copied to a shared
  drive and synced to OneDrive without anyone deciding to do that. The PDF
  Fixer app already stored its token DPAPI-encrypted; this brings the
  PowerShell side to the same bar.

  HOW: Windows DPAPI at user scope, via PowerShell's own SecureString
  round-trip (ConvertFrom-SecureString with no -Key IS DPAPI). The blob only
  decrypts for the SAME Windows user on the SAME machine, so a copied folder
  is useless to anyone else - which also means a token must be re-entered
  after a machine rebuild. That is the intended trade.

  Files:
    canvas.token.enc   the DPAPI blob (this is what gets written now)
    canvas.token       legacy plaintext; auto-migrated into .enc and DELETED

  ASCII only. PowerShell 5.1 compatible.
#>

function Protect-CanvasToken {
    param([Parameter(Mandatory)][string]$Token)
    $secure = ConvertTo-SecureString -String $Token -AsPlainText -Force
    return (ConvertFrom-SecureString -SecureString $secure)
}

function Unprotect-CanvasToken {
    param([Parameter(Mandatory)][string]$Blob)
    $secure = ConvertTo-SecureString -String $Blob -ErrorAction Stop
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try   { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Save-CanvasToken {
    <# Write the encrypted token into $Dir and remove any plaintext copy. #>
    param(
        [Parameter(Mandatory)][string]$Dir,
        [Parameter(Mandatory)][string]$Token
    )
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Force $Dir | Out-Null }
    $enc = Join-Path $Dir 'canvas.token.enc'
    # ASCII, no BOM, no trailing newline - the blob is a plain hex string
    [IO.File]::WriteAllText($enc, (Protect-CanvasToken -Token $Token),
                            (New-Object System.Text.ASCIIEncoding))
    $plain = Join-Path $Dir 'canvas.token'
    if (Test-Path $plain) {
        try { Remove-Item -Force $plain -ErrorAction Stop } catch {
            Write-Warning ("Could not delete the plaintext token at {0} - delete it by hand." -f $plain)
        }
    }
    return $enc
}

function Get-CanvasToken {
    <#
      Resolve a token for a folder (or an explicit file).
        -TokenPath : an explicit file. .enc is decrypted; anything else is
                     read as plaintext (kept for automation/CI that injects
                     a token file deliberately).
        -Dir       : look for canvas.token.enc, else migrate canvas.token.
      Returns @{ Token; Path; Encrypted } or throws with an actionable message.
    #>
    param([string]$TokenPath, [string]$Dir)

    if ($TokenPath) {
        if (-not (Test-Path $TokenPath)) {
            throw ("Token file not found: {0}" -f $TokenPath)
        }
        $raw = (Get-Content -Raw -Encoding ASCII $TokenPath).Trim()
        if ($TokenPath -like '*.enc') {
            try { return @{ Token = (Unprotect-CanvasToken -Blob $raw); Path = $TokenPath; Encrypted = $true } }
            catch {
                throw ("Could not decrypt {0}. An encrypted token only opens for the Windows account and machine that created it. Re-run Setup-Canvas.ps1 to store a fresh one." -f $TokenPath)
            }
        }
        return @{ Token = $raw; Path = $TokenPath; Encrypted = $false }
    }

    if (-not $Dir) { throw "Get-CanvasToken needs -TokenPath or -Dir." }
    $enc   = Join-Path $Dir 'canvas.token.enc'
    $plain = Join-Path $Dir 'canvas.token'

    if (Test-Path $enc) {
        $raw = (Get-Content -Raw -Encoding ASCII $enc).Trim()
        try { return @{ Token = (Unprotect-CanvasToken -Blob $raw); Path = $enc; Encrypted = $true } }
        catch {
            if (-not (Test-Path $plain)) {
                throw ("Could not decrypt {0}. An encrypted token only opens for the Windows account and machine that created it. Re-run Setup-Canvas.ps1 to store a fresh one." -f $enc)
            }
            # fall through to the plaintext and re-encrypt it for this account
        }
    }

    if (Test-Path $plain) {
        $raw = (Get-Content -Raw $plain).Trim()
        if (-not $raw) { throw ("The token file {0} is empty." -f $plain) }
        # migrate: encrypt for this account, delete the plaintext
        try {
            $encPath = Save-CanvasToken -Dir $Dir -Token $raw
            Write-Host ("  CanvasToken: encrypted the plaintext canvas.token for this Windows account -> {0} (plaintext deleted)" -f (Split-Path -Leaf $encPath))
            return @{ Token = $raw; Path = $encPath; Encrypted = $true }
        } catch {
            Write-Warning ("Could not encrypt the token ({0}); continuing with the plaintext file." -f $_.Exception.Message)
            return @{ Token = $raw; Path = $plain; Encrypted = $false }
        }
    }

    throw ("No Canvas token found in {0} (looked for canvas.token.enc and canvas.token). Run Setup-Canvas.ps1 first, or pass -TokenPath." -f $Dir)
}
