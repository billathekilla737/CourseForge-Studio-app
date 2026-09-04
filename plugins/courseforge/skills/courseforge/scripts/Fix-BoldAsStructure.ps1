<#
  Fix-BoldAsStructure.ps1 - clear Ally's "Styles might be used instead of semantic markup
  for structure" across a course, WITHOUT the usual mistake of promoting everything to a
  heading.

  What trips the check: a paragraph whose entire content is one emphasis element,
  <p><strong>...</strong></p> or <p><em>...</em></p>. Length is irrelevant. Bolding a word
  or phrase INSIDE a sentence is fine - Ally says so explicitly. The flag is for the
  whole block.

  Three different things hide behind that one flag, and they need three different fixes
  (see references/ada-remediation.md). One real course had ~70 wholly-bold paragraphs and
  only ~16 wanted a heading:

     short structural label   -> promote to a real <h3>          -PromoteLabels
     instruction sentence     -> drop the blanket <strong>        -UnboldSentences
     code faked with &nbsp;   -> collapse into one code block     -ConvertCodeRuns

  DEFAULT BEHAVIOUR IS REPORT ONLY. It classifies every hit so a human can triage, because
  a blanket rule misfires: "*Updated: February 7, 2025*" is not a heading, "I will go
  first:" in a discussion is not structure, and "public new string ToString()" is code.

  -PromoteLabels uses an ALLOWLIST (-Labels), never a length heuristic, and refuses to
  promote in a body that has no <h2> - otherwise you clear this flag and raise
  "Page contains skipped headings" instead.

  Covers pages, assignment descriptions, and discussion topics. Every write is gated on the
  visible text being byte-identical, and the Canvas theme <link>/<script> is stripped first.

  Usage (from the folder holding canvas.token + canvas.config.<id>.json):
      .\Fix-BoldAsStructure.ps1                                   # report + classify
      .\Fix-BoldAsStructure.ps1 -TitleFilter 'Highlights|Hints'   # narrow the sweep
      .\Fix-BoldAsStructure.ps1 -PromoteLabels -Apply
      .\Fix-BoldAsStructure.ps1 -UnboldSentences -ConvertCodeRuns -Apply

  ASCII only. PowerShell 5.1 compatible. Dry-run unless -Apply.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$TitleFilter,                       # regex; limits which items are touched
    [string[]]$Labels = @('^You Do It \d+$','^Instructions:$','^HINTS:$','^SAMPLE:$','^Sample output:$'),
    [int]$SentenceMinLength = 40,               # below this, a hit is treated as a label candidate
    [switch]$PromoteLabels,
    [switch]$UnboldSentences,
    [switch]$ConvertCodeRuns,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx    = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg    = $ctx.Config
$hdr    = @{ Authorization = ("Bearer " + (Get-Content -Raw $ctx.TokenPath).Trim()) }
$base   = $cfg.base_url.TrimEnd('/') + '/api/v1'
$course = [string]$cfg.course_id

$WHOLLY   = '<p[^>]*>\s*<(strong|b|em|i)\b[^>]*>(.*?)</\1>\s*</p>'
$H3_STYLE = 'margin: 18px 0 8px; font-size: 17px; color: #061E3F;'
# clean-look code block: bordered, monospace, NO background fill (a fill would raise the
# "use of color" advisory)
$CODE_STYLE = "margin-top: 12px; padding: 12px 14px; border-radius: 8px; border: 1px solid #d7dce3; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; color: #2c3a4d; white-space: pre-wrap; overflow-x: auto;"

function J-Str([string]$s) {
    $sb = New-Object System.Text.StringBuilder; [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        switch ($ch) { '"' {[void]$sb.Append('\"')} '\' {[void]$sb.Append('\\')}
            "`n" {[void]$sb.Append('\n')} "`r" {[void]$sb.Append('\r')} "`t" {[void]$sb.Append('\t')}
            default { $i=[int]$ch; if ($i -lt 32 -or $i -gt 126) {[void]$sb.Append(('\u{0:x4}' -f $i))} else {[void]$sb.Append($ch)} } }
    }
    [void]$sb.Append('"'); return $sb.ToString()
}
function Strip-Theme([string]$h) {
    $h = [regex]::Replace($h, '<link[^>]*instructure-uploads[^>]*>', '')
    $h = [regex]::Replace($h, '<script[^>]*instructure-uploads[^>]*>\s*</script>', '')
    return $h
}
function Vis([string]$h) { return (($h -replace '<[^>]+>','') -replace '&nbsp;',' ' -replace '\s+',' ').Trim() }
function Looks-Code([string]$t) { return ($t -match '[{};]|\breturn\b|\(\s*\)|\bpublic\s+\w|\bvoid\b') }
function Is-Label([string]$t) { foreach ($rx in $Labels) { if ($t -match $rx) { return $true } }; return $false }
function Classify([string]$t) {
    if (Looks-Code $t) { return 'code' }
    if (Is-Label $t)   { return 'label' }
    if ($t.Length -ge $SentenceMinLength) { return 'sentence' }
    return 'unclassified'
}

function Fix-CodeRuns([string]$html, [ref]$made) {
    while ($true) {
        $ms = [regex]::Matches($html, $WHOLLY, 'Singleline')
        $run = @()
        for ($i = 0; $i -lt $ms.Count; $i++) {
            $t = Vis $ms[$i].Groups[2].Value
            if ($t.Length -gt 0 -and (Looks-Code $t)) {
                if ($run.Count -eq 0) { $run = @($ms[$i]) }
                else {
                    $prev = $run[-1]
                    $between = $html.Substring($prev.Index + $prev.Length, $ms[$i].Index - ($prev.Index + $prev.Length))
                    if ($between -match '^\s*$') { $run += $ms[$i] } else { break }
                }
            } elseif ($run.Count -gt 0) { break }
        }
        if ($run.Count -eq 0) { break }
        $lines = @()
        foreach ($m in $run) {
            $inner = $m.Groups[2].Value -replace '<br\s*/?>','' -replace '&nbsp;',' ' -replace '<[^>]+>',''
            $inner = $inner -replace '&amp;','&' -replace '&lt;','<' -replace '&gt;','>'
            $inner = $inner.TrimEnd()
            $lead = 0; if ($inner -match '^(\s+)') { $lead = $matches[1].Length }
            $lines += (' ' * [math]::Min($lead, 24)) + $inner.TrimStart()
        }
        $code  = ($lines -join "`n") -replace '&','&amp;' -replace '<','&lt;' -replace '>','&gt;'
        $block = '<div style="' + $CODE_STYLE + '">' + $code + '</div>'
        $s = $run[0].Index; $e = $run[-1].Index + $run[-1].Length
        $html = $html.Substring(0, $s) + $block + $html.Substring($e)
        $made.Value += ("code block ({0} lines)" -f $lines.Count)
    }
    return $html
}
function Promote-Labels([string]$html, [ref]$made) {
    if (([regex]::Matches($html,'<h2')).Count -eq 0) {
        $made.Value += 'SKIPPED promotion: body has no <h2>, an <h3> would be a skipped level'
        return $html
    }
    while ($true) {
        $hit = $null
        foreach ($m in [regex]::Matches($html, $WHOLLY, 'Singleline')) {
            if (Is-Label (Vis $m.Groups[2].Value)) { $hit = $m; break }
        }
        if (-not $hit) { break }
        $t = Vis $hit.Groups[2].Value
        $html = $html.Substring(0, $hit.Index) + '<h3 style="' + $H3_STYLE + '">' + $t + '</h3>' + $html.Substring($hit.Index + $hit.Length)
        $made.Value += ("h3: " + $t)
    }
    return $html
}
function Unbold-Sentences([string]$html, [ref]$made) {
    while ($true) {
        $hit = $null
        foreach ($m in [regex]::Matches($html, $WHOLLY, 'Singleline')) {
            $t = Vis $m.Groups[2].Value
            if ((Classify $t) -eq 'sentence') { $hit = $m; break }
        }
        if (-not $hit) { break }
        $inner = [regex]::Replace($hit.Groups[2].Value, '^\s*<(em|i|strong|b)\b[^>]*>(.*)</\1>\s*$', '$2', 'Singleline')
        $html = $html.Substring(0, $hit.Index) + '<p>' + $inner + '</p>' + $html.Substring($hit.Index + $hit.Length)
        $t = Vis $inner
        $made.Value += ("unbolded: " + $(if ($t.Length -gt 52) { $t.Substring(0,52) + '...' } else { $t }))
    }
    return $html
}
function Get-All([string]$path) {
    $out=@(); $page=1
    while ($true) {
        $sep = if ($path -match '\?') { '&' } else { '?' }
        $raw = Invoke-RestMethod -Uri "$base/courses/$course/$path$sep`per_page=100&page=$page" -Headers $hdr
        $a=@($raw); if ($a.Count -eq 0) { break }
        $out+=$a; if ($a.Count -lt 100) { break }; $page++
    }
    return @($out)
}

$doFix = ($PromoteLabels -or $UnboldSentences -or $ConvertCodeRuns)
Write-Host ("Course {0} - bold-as-structure triage" -f $course)
Write-Host ("mode: {0}{1}" -f $(if ($doFix) { if ($Apply) { 'APPLY' } else { 'DRY RUN' } } else { 'REPORT ONLY' }), `
                             $(if ($doFix) { "  (promote=$PromoteLabels unbold=$UnboldSentences code=$ConvertCodeRuns)" } else { '' }))
Write-Host ""

$targets = @()
foreach ($p in (Get-All 'pages')) {
    $f = Invoke-RestMethod -Uri "$base/courses/$course/pages/$($p.url)" -Headers $hdr
    $targets += [pscustomobject]@{ kind='PAGE'; label=$p.title; body=$f.body; url="$base/courses/$course/pages/$($p.url)"; wrap='{"wiki_page":{"body":"__BODY__"}}' }
}
foreach ($a in (Get-All 'assignments')) {
    if ($a.quiz_id -or $a.discussion_topic) { continue }
    $targets += [pscustomobject]@{ kind='ASGN'; label=$a.name; body=$a.description; url="$base/courses/$course/assignments/$($a.id)"; wrap='{"assignment":{"description":"__BODY__"}}' }
}
foreach ($t in (Get-All 'discussion_topics')) {
    $targets += [pscustomobject]@{ kind='DISC'; label=$t.title; body=$t.message; url="$base/courses/$course/discussion_topics/$($t.id)"; wrap='{"message":"__BODY__"}' }
}

$counts = @{ label=0; sentence=0; code=0; unclassified=0 }
$changedItems = 0
foreach ($t in $targets) {
    if (-not $t.body) { continue }
    if ($TitleFilter -and ($t.label -notmatch $TitleFilter)) { continue }
    $hits = @([regex]::Matches($t.body, $WHOLLY, 'Singleline') | Where-Object { (Vis $_.Groups[2].Value).Length -gt 0 })
    if ($hits.Count -eq 0) { continue }

    Write-Host ("{0}  {1}" -f $t.kind, $t.label)
    foreach ($m in $hits) {
        $v = Vis $m.Groups[2].Value
        $c = Classify $v
        $counts[$c]++
        Write-Host ("   [{0,-12}] len={1,-4} '{2}'" -f $c, $v.Length, $(if ($v.Length -gt 56) { $v.Substring(0,56)+'...' } else { $v }))
    }

    if (-not $doFix) { Write-Host ""; continue }

    $made = @()
    $clean = Strip-Theme $t.body
    if ($ConvertCodeRuns)  { $clean = Fix-CodeRuns      $clean ([ref]$made) }
    if ($PromoteLabels)    { $clean = Promote-Labels    $clean ([ref]$made) }
    if ($UnboldSentences)  { $clean = Unbold-Sentences  $clean ([ref]$made) }
    if ($made.Count -eq 0) { Write-Host ""; continue }

    if ((Vis $t.body) -ne (Vis $clean)) {
        Write-Host "   ! visible text would change - SKIPPED"; Write-Host ""; continue
    }
    foreach ($m in $made) { Write-Host ("   -> {0}" -f $m) }
    $left = @([regex]::Matches($clean, $WHOLLY, 'Singleline') | Where-Object { (Vis $_.Groups[2].Value).Length -gt 0 }).Count
    Write-Host ("   wholly-emphasized paragraphs remaining: {0}" -f $left)
    if ($Apply) {
        try {
            Invoke-RestMethod -Method Put -Uri $t.url -Headers $hdr -ContentType 'application/json; charset=utf-8' `
                -Body ([Text.Encoding]::UTF8.GetBytes($t.wrap.Replace('"__BODY__"', (J-Str $clean)))) | Out-Null
        } catch { Write-Host ("   ! PUT failed: {0}" -f $_.Exception.Message); Write-Host ""; continue }
    }
    $changedItems++
    Write-Host ""
}

Write-Host ""
Write-Host ("hits by class: label={0}  sentence={1}  code={2}  unclassified={3}" -f `
    $counts['label'], $counts['sentence'], $counts['code'], $counts['unclassified'])
if (-not $doFix) {
    Write-Host ""
    Write-Host "REPORT ONLY. Triage the list above, then re-run with the switches you want:"
    Write-Host "  -PromoteLabels     promote allowlisted labels to <h3> (add yours via -Labels)"
    Write-Host "  -UnboldSentences   drop the blanket bold on instruction sentences"
    Write-Host "  -ConvertCodeRuns   collapse bolded code into one code block"
    Write-Host "  ... plus -Apply to write. 'unclassified' hits are for a human to look at."
} elseif (-not $Apply) {
    Write-Host ""
    Write-Host "DRY RUN - nothing written. Add -Apply."
} else {
    Write-Host ("items changed: {0}" -f $changedItems)
}
