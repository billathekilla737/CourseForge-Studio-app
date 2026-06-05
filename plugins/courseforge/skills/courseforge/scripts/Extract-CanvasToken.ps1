# Extract-CanvasToken.ps1
# Pulls the Canvas API token out of "Canvas Token.rtf" (RTF splits it across
# formatting runs) and writes it as plain text to canvas.token for the uploader.
# Prints only a masked confirmation -- never the full token.

param(
    [string]$RtfPath   = (Join-Path $PSScriptRoot '..\Canvas Token.rtf'),
    [string]$OutPath   = (Join-Path $PSScriptRoot '..\canvas.token')
)

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

Set-Content -Path $OutPath -Value $token -NoNewline -Encoding ascii
$prefix = $token.Substring(0, [Math]::Min(5, $token.Length))
Write-Host "Token captured -> $OutPath"
Write-Host ("  prefix: {0}...  length: {1} chars" -f $prefix, $token.Length)
Write-Host "  (keep canvas.token private; rotate the token in Canvas when the project is done)"
