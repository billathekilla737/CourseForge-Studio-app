# Extract-CanvasToken.ps1
# Pulls the Canvas API token out of "Canvas Token.rtf" (RTF splits it across
# formatting runs) and stores it ENCRYPTED as canvas.token.enc, the same way
# Setup-Canvas.ps1 does. Prints only a masked confirmation -- never the token.
#
# It used to write plaintext to canvas.token, which defeated the point of
# encrypting it everywhere else. It now goes through Save-CanvasToken, and
# offers to remove the RTF - which is itself an unencrypted copy of the token.

param(
    [string]$RtfPath   = (Join-Path $PSScriptRoot '..\Canvas Token.rtf'),
    [string]$OutDir    = (Join-Path $PSScriptRoot '..'),
    [switch]$KeepRtf           # keep the source RTF (it holds the raw token)
)

. "$PSScriptRoot\CanvasToken.ps1"

if (-not (Test-Path $RtfPath)) { Write-Error "RTF not found: $RtfPath"; exit 1 }

$raw = Get-Content -Raw -Path $RtfPath

# Only look at the document body, before the embedded theme/font hex blob.
$cut = $raw.IndexOf('{\*\themedata')
if ($cut -gt 0) { $raw = $raw.Substring(0, $cut) }

# Strip RTF: control words (\word, \word123, \word-12), \* markers, and braces.
$txt = $raw -replace '\\\*', ' '
$txt = $txt -replace '\\[a-zA-Z]+-?\d* ?', ' '
$txt = $txt -replace '[{}]', ' '

# Token fragments are separated only by (now-removed) control words; collapse
# whitespace so the pieces rejoin, then match the Canvas token shape: NN~xxxx...
$compact = ($txt -replace '\s', '')
if ($compact -match '(\d{2,6}~[A-Za-z0-9]{40,90})') {
    $token = $Matches[1]
} else {
    Write-Error "Could not locate a Canvas-token-shaped string in the RTF. Save the token as plain text in canvas.token instead."
    exit 1
}

$encPath = Save-CanvasToken -Dir $OutDir -Token $token
$prefix = $token.Substring(0, [Math]::Min(5, $token.Length))
Write-Host "Token captured (encrypted for this Windows account) -> $encPath"
Write-Host ("  prefix: {0}...  length: {1} chars" -f $prefix, $token.Length)

if ($KeepRtf) {
    Write-Warning ("{0} still contains the token in plain text. Delete it when you are done." -f $RtfPath)
} else {
    try {
        Remove-Item -LiteralPath $RtfPath -Force -ErrorAction Stop
        Write-Host ("  removed the source RTF (it held the token unencrypted): {0}" -f (Split-Path -Leaf $RtfPath))
    } catch {
        Write-Warning ("Could not delete {0} - it holds the token in plain text; remove it by hand." -f $RtfPath)
    }
}
Write-Host "  (rotate the token in Canvas when the project is done)"
