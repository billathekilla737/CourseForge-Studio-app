<#
  Remove-BorderedBoxes.ps1 - strip inline "box" borders used as emphasis from every HTML
  body in a course.

  The antipattern:

      <span style="border: 1px solid #d7dce3">some sentence</span>

  A border on an INLINE span fragments when the text wraps, so a sentence ends up in a thin
  box with ragged open edges - it reads as a rendering bug. Worse, it signals emphasis by
  decoration alone, which a screen reader announces as nothing, so the emphasis does not
  exist for anyone not looking at it (the "never meaning by color alone" rule in
  references/style-guide.md). It is not a component in the style guide; it is stray RCE
  formatting, usually from pasting out of Word or a prior course.

  One real course carried 80 of these across 22 items - pages, assignment descriptions and
  a discussion - so assume it is course-wide rather than a one-page problem.

  Surgical by design:
      style is ONLY the border      -> unwrap the span (tags dropped, content kept)
      style has other declarations  -> delete just the border declaration, keep the span
                                       and its remaining styling (e.g. font-size: 14pt)

  Never touched:
      * <div> cards and heroes. The card component legitimately uses
        "border: 1px solid #d7dce3; border-top: 4px solid #E9A821" - only SPANS are considered.
      * Pills, which ring in gold rather than the grey card border.
    Pass -BorderColor to target a different institution's stray border colour.

  Covers pages, discussions/announcements, quiz descriptions, quiz question text,
  assignment descriptions, and the syllabus body. Quiz/discussion-backed assignment shells
  are skipped (they 400 on assignment[description]); their quiz or topic is handled instead.

  Every write is gated on the visible text being byte-identical afterwards, and the Canvas
  theme <link>/<script> is stripped first so it is not stored and double-injected.

  Usage (from the folder holding canvas.token + canvas.config.<id>.json):
      .\Remove-BorderedBoxes.ps1                 # dry run - reports what it would change
      .\Remove-BorderedBoxes.ps1 -Apply

  ASCII only. PowerShell 5.1 compatible. Dry-run by default; -Apply to write.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$BorderColor = '#d7dce3',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx    = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg    = $ctx.Config
$hdr    = @{ Authorization = ("Bearer " + $ctx.Token) }   # decrypted in memory; the file is a DPAPI blob
$base   = $cfg.base_url.TrimEnd('/') + '/api/v1'
$course = [string]$cfg.course_id

$SPAN = '<span([^>]*)style="([^"]*)"([^>]*)>'
$BD   = 'border\s*:\s*1px\s+solid\s+' + [regex]::Escape($BorderColor) + '\s*;?'

function J-Str([string]$s) {
    $sb = New-Object System.Text.StringBuilder; [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        switch ($ch) {
            '"' {[void]$sb.Append('\"')} '\' {[void]$sb.Append('\\')}
            "`n" {[void]$sb.Append('\n')} "`r" {[void]$sb.Append('\r')} "`t" {[void]$sb.Append('\t')}
            default { $i=[int]$ch
                      if ($i -lt 32 -or $i -gt 126) {[void]$sb.Append(('\u{0:x4}' -f $i))}
                      else {[void]$sb.Append($ch)} }
        }
    }
    [void]$sb.Append('"'); return $sb.ToString()
}
function Strip-Theme([string]$h) {
    $h = [regex]::Replace($h, '<link[^>]*instructure-uploads[^>]*>', '')
    $h = [regex]::Replace($h, '<script[^>]*instructure-uploads[^>]*>\s*</script>', '')
    return $h
}
function Vis([string]$h) { return (($h -replace '<[^>]+>','') -replace '\s+',' ').Trim() }
function Count-Boxes([string]$h) {
    if (-not $h) { return 0 }
    $n = 0
    foreach ($m in [regex]::Matches($h, $SPAN, 'IgnoreCase')) { if ($m.Groups[2].Value -match $BD) { $n++ } }
    return $n
}
function Fix-Boxes([string]$html) {
    while ($true) {
        $target = $null
        foreach ($m in [regex]::Matches($html, $SPAN, 'IgnoreCase')) {
            if ($m.Groups[2].Value -match $BD) { $target = $m; break }
        }
        if (-not $target) { break }
        $reduced = ([regex]::Replace($target.Groups[2].Value, $BD, '')).Trim()
        $reduced = ($reduced -replace '^\s*;\s*','' -replace ';\s*;',';').Trim()

        if ($reduced -match '^[\s;]*$') {
            # unwrap: walk to the MATCHING </span> with a depth counter. A non-greedy regex
            # would stop at the first inner </span> and mangle nested markup.
            $afterOpen = $target.Index + $target.Length
            $depth = 1; $pos = $afterOpen; $cs = -1; $ce = -1
            while ($depth -gt 0) {
                $nxt = [regex]::Match($html.Substring($pos), '<span\b|</span\s*>', 'IgnoreCase')
                if (-not $nxt.Success) { break }
                $abs = $pos + $nxt.Index
                if ($nxt.Value -match '^</') { $depth-- } else { $depth++ }
                $pos = $abs + $nxt.Length
                if ($depth -eq 0) { $cs = $abs; $ce = $pos }
            }
            if ($cs -lt 0) { Write-Host "     ! unbalanced span - leaving this body alone"; break }
            $html = $html.Substring(0, $target.Index) + $html.Substring($afterOpen, $cs - $afterOpen) + $html.Substring($ce)
        } else {
            $newTag = '<span' + $target.Groups[1].Value + 'style="' + $reduced + '"' + $target.Groups[3].Value + '>'
            $html = $html.Substring(0, $target.Index) + $newTag + $html.Substring($target.Index + $target.Length)
        }
    }
    return $html
}
function Get-All([string]$path) {
    $out = @(); $page = 1
    while ($true) {
        $sep = if ($path -match '\?') { '&' } else { '?' }
        # assign THEN wrap (gotcha 2)
        $raw = Invoke-RestMethod -Uri "$base/courses/$course/$path$sep`per_page=100&page=$page" -Headers $hdr
        $a = @($raw); if ($a.Count -eq 0) { break }
        $out += $a; if ($a.Count -lt 100) { break }; $page++
    }
    return @($out)
}

$items = 0; $boxes = 0; $skipped = @()
function Do-Fix($label, $body, $putUrl, $jsonWrap) {
    $n = Count-Boxes $body
    if ($n -eq 0) { return }
    $clean = Fix-Boxes (Strip-Theme $body)
    if ((Vis $body) -ne (Vis $clean)) {
        Write-Host ("   ! {0}: visible text would change - SKIPPED" -f $label); $script:skipped += $label; return
    }
    Write-Host ("   {0,-58} {1} -> {2}" -f $label, $n, (Count-Boxes $clean))
    if ($Apply) {
        try {
            Invoke-RestMethod -Method Put -Uri $putUrl -Headers $hdr `
                -ContentType 'application/json; charset=utf-8' `
                -Body ([Text.Encoding]::UTF8.GetBytes($jsonWrap.Replace('"__BODY__"', (J-Str $clean)))) | Out-Null
        } catch {
            Write-Host ("     ! PUT failed: {0}" -f $_.Exception.Message); $script:skipped += $label; return
        }
    }
    $script:items++; $script:boxes += $n
}

Write-Host ("Course {0} - remove inline bordered-box emphasis ({1})" -f $course, $BorderColor)
Write-Host ("mode: {0}" -f $(if ($Apply) { 'APPLY' } else { 'DRY RUN' }))

Write-Host ""; Write-Host "== pages =="
foreach ($p in (Get-All 'pages')) {
    $f = Invoke-RestMethod -Uri "$base/courses/$course/pages/$($p.url)" -Headers $hdr
    Do-Fix "PAGE  $($p.title)" $f.body "$base/courses/$course/pages/$($p.url)" '{"wiki_page":{"body":"__BODY__"}}'
}

Write-Host ""; Write-Host "== discussions and announcements =="
$topics = @(); $topics += Get-All 'discussion_topics'; $topics += Get-All 'discussion_topics?only_announcements=true'
foreach ($t in ($topics | Sort-Object id -Unique)) {
    Do-Fix "DISC  $($t.title)" $t.message "$base/courses/$course/discussion_topics/$($t.id)" '{"message":"__BODY__"}'
}

Write-Host ""; Write-Host "== quizzes and quiz questions =="
$quizzes = Get-All 'quizzes'
foreach ($q in $quizzes) {
    Do-Fix "QUIZ  $($q.title)" $q.description "$base/courses/$course/quizzes/$($q.id)" '{"quiz":{"description":"__BODY__"}}'
    $qs = @(); $page = 1
    while ($true) {
        $raw = Invoke-RestMethod -Uri "$base/courses/$course/quizzes/$($q.id)/questions?per_page=100&page=$page" -Headers $hdr
        $a = @($raw); if ($a.Count -eq 0) { break }
        $qs += $a; if ($a.Count -lt 100) { break }; $page++
    }
    foreach ($qq in $qs) {
        Do-Fix "QQ    [$($q.title)] $($qq.question_name)" $qq.question_text `
            "$base/courses/$course/quizzes/$($q.id)/questions/$($qq.id)" '{"question":{"question_text":"__BODY__"}}'
    }
}

Write-Host ""; Write-Host "== assignments =="
foreach ($a in (Get-All 'assignments')) {
    if ($a.quiz_id -or $a.discussion_topic) { continue }   # shells 400 on description writes
    Do-Fix "ASGN  $($a.name)" $a.description "$base/courses/$course/assignments/$($a.id)" '{"assignment":{"description":"__BODY__"}}'
}

Write-Host ""; Write-Host "== syllabus =="
$c = Invoke-RestMethod -Uri "$base/courses/$course`?include%5B%5D=syllabus_body" -Headers $hdr
Do-Fix "SYLL  syllabus_body" $c.syllabus_body "$base/courses/$course" '{"course":{"syllabus_body":"__BODY__"}}'

Write-Host ""
Write-Host ("items changed: {0}   boxes removed: {1}   skipped: {2}" -f $items, $boxes, $skipped.Count)
foreach ($s in $skipped) { Write-Host ("   skipped: {0}" -f $s) }
if (-not $Apply) { Write-Host ""; Write-Host "DRY RUN - nothing written. Re-run with -Apply." }
