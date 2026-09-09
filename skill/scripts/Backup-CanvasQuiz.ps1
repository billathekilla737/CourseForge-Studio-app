<#
  Backup-CanvasQuiz.ps1 - make an UNPUBLISHED in-course snapshot of a Classic Quiz before
  you rewrite it.

  Why this exists: POST /assignments/:id/duplicate returns 400 for quiz-backed assignments,
  so Classic Quizzes cannot be duplicated the easy way (see references/canvas-api-gotchas.md
  gotcha 5). This builds the copy explicitly - create a new unpublished quiz carrying the
  same settings, then re-POST every question.

  The copy is ALWAYS created unpublished, whatever the source's state, so it can never
  become student-visible by accident.

  Handles multiple_choice, true_false, short_answer, multiple_answers, matching, essay,
  numerical, and text_only questions. The READ shape of an answer is not the WRITE shape
  (answers[].text/.left/.right/.weight vs answer_text/answer_match_left/answer_match_right/
  answer_weight) - that mapping is the whole trick, and getting it wrong yields blank
  answers with no error (gotcha 6).

  Usage (from the folder holding canvas.token + canvas.config.<id>.json):
      .\Backup-CanvasQuiz.ps1 -QuizId 5414535                 # dry run
      .\Backup-CanvasQuiz.ps1 -QuizId 5414535 -Apply
      .\Backup-CanvasQuiz.ps1 -QuizId 5414535 -CourseId 735052 -TitlePrefix 'PRE-REWRITE' -Apply

  Prints BACKUP_QUIZ_ID=<id> on success so a caller can chain to Rewrite/Push steps.

  ASCII only. PowerShell 5.1 compatible. Dry-run by default; -Apply to write.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$QuizId,
    [string]$ConfigPath,
    [string]$TokenPath,
    [string]$CourseId,
    [string]$TitlePrefix = 'BACKUP',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx    = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$cfg    = $ctx.Config
$token  = $ctx.Token     # decrypted in memory by CanvasContext; the file is a DPAPI blob
$hdr    = @{ Authorization = "Bearer $token" }
$base   = $cfg.base_url.TrimEnd('/') + '/api/v1'
$course = [string]$cfg.course_id

# --- JSON writer. ConvertTo-Json mangles long strings into {"value":...} and Canvas then
#     silently ignores the field (200, nothing saved). See gotcha 1.
function J-Str([string]$s) {
    if ($null -eq $s) { return 'null' }
    $sb = New-Object System.Text.StringBuilder; [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        switch ($ch) {
            '"'  { [void]$sb.Append('\"') } '\'  { [void]$sb.Append('\\') }
            "`b" { [void]$sb.Append('\b') } "`f" { [void]$sb.Append('\f') }
            "`n" { [void]$sb.Append('\n') } "`r" { [void]$sb.Append('\r') } "`t" { [void]$sb.Append('\t') }
            default { $i = [int]$ch
                      if ($i -lt 32 -or $i -gt 126) { [void]$sb.Append(('\u{0:x4}' -f $i)) }
                      else { [void]$sb.Append($ch) } }
        }
    }
    [void]$sb.Append('"'); return $sb.ToString()
}
function J-Val($v) {
    if ($null -eq $v) { return 'null' }
    if ($v -is [bool]) { if ($v) { return 'true' } else { return 'false' } }
    if ($v -is [int] -or $v -is [long] -or $v -is [double] -or $v -is [decimal]) {
        return [string]::Format([cultureinfo]::InvariantCulture, '{0}', $v)
    }
    return (J-Str ([string]$v))
}
function Send-Json([string]$method, [string]$url, [string]$json) {
    return Invoke-RestMethod -Method $method -Uri $url -Headers $hdr `
        -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json))
}
function Get-Questions([string]$qid) {
    # assign THEN wrap - @(Invoke-RestMethod ...) inline nests the array in PS 5.1 (gotcha 2)
    $out = @(); $page = 1
    while ($true) {
        $raw = Invoke-RestMethod -Uri "$base/courses/$course/quizzes/$qid/questions?per_page=100&page=$page" -Headers $hdr
        $a = @($raw); if ($a.Count -eq 0) { break }
        $out += $a; if ($a.Count -lt 100) { break }; $page++
    }
    return @($out)
}

# --- read the source ---------------------------------------------------------------
$src = Invoke-RestMethod -Uri "$base/courses/$course/quizzes/$QuizId" -Headers $hdr
$questions = Get-Questions $QuizId
$newTitle  = "$TitlePrefix - " + $src.title

Write-Host ("SOURCE : [{0}] {1}" -f $src.id, $src.title)
Write-Host ("         {0} questions, type={1}, published={2}" -f $questions.Count, $src.quiz_type, $src.published)
Write-Host ("         question points sum: {0}" -f (($questions | Measure-Object points_possible -Sum).Sum))
$byType = $questions | Group-Object question_type | ForEach-Object { "$($_.Name)=$($_.Count)" }
Write-Host ("         types: {0}" -f ($byType -join ', '))
Write-Host ("TARGET : '{0}'  (always created unpublished)" -f $newTitle)

if ($questions.Count -eq 0) { Write-Host ""; Write-Host "Source quiz has no questions - nothing to back up."; exit 1 }
if (-not $Apply) { Write-Host ""; Write-Host "DRY RUN - nothing written. Re-run with -Apply."; exit 0 }

# --- create the backup quiz --------------------------------------------------------
$qs = @()
$qs += '"title":'                  + (J-Val $newTitle)
$qs += '"quiz_type":'              + (J-Val $src.quiz_type)
$qs += '"published":false'
$qs += '"description":'            + (J-Val ("<p><strong>Unpublished backup</strong> of the original quiz, kept for reference.</p>" + [string]$src.description))
$qs += '"shuffle_answers":'        + (J-Val $src.shuffle_answers)
$qs += '"allowed_attempts":'       + (J-Val $src.allowed_attempts)
$qs += '"scoring_policy":'         + (J-Val $src.scoring_policy)
$qs += '"one_question_at_a_time":' + (J-Val $src.one_question_at_a_time)
$qs += '"cant_go_back":'           + (J-Val $src.cant_go_back)
$qs += '"hide_results":'           + (J-Val $src.hide_results)
if ($null -ne $src.time_limit)               { $qs += '"time_limit":'               + (J-Val $src.time_limit) }
if ($src.access_code)                        { $qs += '"access_code":'              + (J-Val $src.access_code) }
if ($src.ip_filter)                          { $qs += '"ip_filter":'                + (J-Val $src.ip_filter) }
if ($null -ne $src.require_lockdown_browser) { $qs += '"require_lockdown_browser":' + (J-Val $src.require_lockdown_browser) }

$new = Send-Json 'POST' "$base/courses/$course/quizzes" ('{"quiz":{' + ($qs -join ',') + '}}')
Write-Host ("  created backup quiz id={0} (published={1})" -f $new.id, $new.published)

# --- copy every question -----------------------------------------------------------
$ok = 0; $fail = 0
foreach ($q in $questions) {
    $f = @()
    $f += '"question_name":'   + (J-Val $q.question_name)
    $f += '"question_text":'   + (J-Val $q.question_text)
    $f += '"question_type":'   + (J-Val $q.question_type)
    $f += '"points_possible":' + (J-Val $q.points_possible)
    foreach ($c in @('correct_comments','incorrect_comments','neutral_comments')) {
        if ($q.$c) { $f += ('"' + $c + '":') + (J-Val $q.$c) }
    }
    if ($q.question_type -eq 'matching_question' -and $q.matching_answer_incorrect_matches) {
        $f += '"matching_answer_incorrect_matches":' + (J-Val $q.matching_answer_incorrect_matches)
    }

    $ans = @()
    foreach ($a in @($q.answers)) {
        $p = @()
        if ($q.question_type -eq 'matching_question') {
            $p += '"answer_match_left":'  + (J-Val $a.left)
            $p += '"answer_match_right":' + (J-Val $a.right)
            $p += '"answer_weight":100'
        } else {
            $txt = $a.text; if (-not $txt) { $txt = $a.html }
            $p += '"answer_text":'   + (J-Val $txt)
            $p += '"answer_weight":' + (J-Val ([int]$a.weight))
            if ($a.comments) { $p += '"answer_comments":' + (J-Val $a.comments) }
        }
        $ans += '{' + ($p -join ',') + '}'
    }
    if ($ans.Count -gt 0) { $f += '"answers":[' + ($ans -join ',') + ']' }

    try {
        $null = Send-Json 'POST' "$base/courses/$course/quizzes/$($new.id)/questions" ('{"question":{' + ($f -join ',') + '}}')
        $ok++
    } catch {
        $fail++
        Write-Host ("  ! failed: {0} ({1}) - {2}" -f $q.question_name, $q.question_type, $_.Exception.Message)
    }
}

# --- verify by reading the copy back ----------------------------------------------
$check  = Get-Questions $new.id
$verify = Invoke-RestMethod -Uri "$base/courses/$course/quizzes/$($new.id)" -Headers $hdr
$srcPts = ($questions | Measure-Object points_possible -Sum).Sum
$cpyPts = ($check     | Measure-Object points_possible -Sum).Sum

Write-Host ""
Write-Host ("RESULT : posted {0} ok, {1} failed" -f $ok, $fail)
Write-Host ("         backup holds {0} questions (source {1})" -f $check.Count, $questions.Count)
Write-Host ("         question points {0} (source {1})" -f $cpyPts, $srcPts)
Write-Host ("         published={0}" -f $verify.published)
if ($check.Count -ne $questions.Count) { Write-Host "         WARNING: question count mismatch - inspect before relying on this backup." }
if ([double]$cpyPts -ne [double]$srcPts) { Write-Host "         WARNING: points differ - inspect before relying on this backup." }
if ($verify.published) { Write-Host "         WARNING: backup is PUBLISHED - it should not be." }
# NOTE: the quiz object's points_possible/question_count stay stale while unpublished
# (gotcha 3), which is why the checks above use /questions instead.
Write-Host ("BACKUP_QUIZ_ID={0}" -f $new.id)
