<#
  Push-CanvasModule.ps1 - generic, dry-run-first pusher for Canvas page / assignment /
  quiz content. Driven entirely by a "changeset" JSON file, so the same script serves
  every module - no more copy-paste-and-sed a previous module's push script.

  DRY RUN BY DEFAULT. Always prints a plan before touching anything. Pass -Apply to write.

  Changeset schema (see examples/changeset.example.json):
  {
    "course_id": "734709", "base_url": "https://mgccc.instructure.com",
    "page":       { "slug": "m7-slash-notes-...", "body_file": "new/page_notes.html" },
    "assignment": { "id": "16099307", "description_file": "new/assignment.html" },
    "quiz": {
      "id": "5381766",
      "description_file": "new/quiz_description.html",   // OR "description" inline
      "questions_file": "new/quiz.json",                  // rebuilds ALL questions
      "expected_question_count": 14,                       // safety check, optional
      "require_unpublished_course": true                   // refuses to rebuild
    }                                                       // questions otherwise
  }
  Any of "page" / "assignment" / "quiz" may be omitted - only what's present is touched.
  Rebuilding quiz questions deletes and recreates them, which would destroy real student
  attempt data; require_unpublished_course (default true) refuses unless the COURSE
  itself is unpublished, which is the only case where attempts cannot exist yet.

  Usage:
    pwsh -File Push-CanvasModule.ps1 -Changeset ./changeset.json              # plan only
    pwsh -File Push-CanvasModule.ps1 -Changeset ./changeset.json -Apply       # write
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Changeset,
    [string]$ConfigPath,
    [string]$TokenPath,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib_canvas.ps1')

$cs = (Read-Utf8File $Changeset) | ConvertFrom-Json
$changesetDir = Split-Path -Parent (Resolve-Path $Changeset).Path

function Resolve-Rel([string]$p) {
    if ([IO.Path]::IsPathRooted($p)) { return $p }
    return (Join-Path $changesetDir $p)
}

$ctx = Resolve-CanvasConfig -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $cs.course_id
$base = if ($cs.base_url) { $cs.base_url.TrimEnd('/') } else { $ctx.BaseUrl }
$cid  = if ($cs.course_id) { "$($cs.course_id)" } else { $ctx.CourseId }
$hdr  = $ctx.Headers

$mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
Write-Host ""
Write-Host "=============================================================="
Write-Host "  Canvas module push - course $cid - $mode"
Write-Host "=============================================================="

$plan = @()

if ($cs.page) {
    $body = Read-Utf8File (Resolve-Rel $cs.page.body_file)
    $cur = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/pages/$($cs.page.slug)" -Headers $hdr
    Write-Host ("  PAGE  '{0}'  {1} -> {2} chars" -f $cur.title, $cur.body.Length, $body.Length)
    $plan += @{ kind = 'page'; slug = $cs.page.slug; body = $body }
}

if ($cs.assignment) {
    $desc = Read-Utf8File (Resolve-Rel $cs.assignment.description_file)
    $cur = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/assignments/$($cs.assignment.id)" -Headers $hdr
    Write-Host ("  ASSIGN '{0}' ({1} pts, due {2}) - points/due UNCHANGED" -f $cur.name, $cur.points_possible, $cur.due_at)
    Write-Host ("       description {0} -> {1} chars" -f $cur.description.Length, $desc.Length)
    $plan += @{ kind = 'assignment'; id = $cs.assignment.id; description = $desc }
}

$quizQuestions = $null
if ($cs.quiz) {
    $cur = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/quizzes/$($cs.quiz.id)" -Headers $hdr
    Write-Host ("  QUIZ  '{0}' ({1} pts)" -f $cur.title, $cur.points_possible)

    $quizDesc = $null
    if ($cs.quiz.description_file) { $quizDesc = Read-Utf8File (Resolve-Rel $cs.quiz.description_file) }
    elseif ($cs.quiz.description) { $quizDesc = $cs.quiz.description }

    if ($cs.quiz.questions_file) {
        $parsed = (Read-Utf8File (Resolve-Rel $cs.quiz.questions_file)) | ConvertFrom-Json
        # accept either a bare array of questions, or a {description, questions:[...]} wrapper
        # (the wrapper is the more natural shape to hand-author since the description lives
        # right next to the questions it describes; both are supported so callers don't have
        # to think about it).
        if ($parsed.PSObject.Properties.Name -contains 'questions') {
            $quizQuestions = @($parsed.questions)
            if (-not $quizDesc -and $parsed.PSObject.Properties.Name -contains 'description') { $quizDesc = $parsed.description }
        } else {
            $quizQuestions = @($parsed)
        }
        $requireUnpub = if ($null -ne $cs.quiz.require_unpublished_course) { [bool]$cs.quiz.require_unpublished_course } else { $true }
        $existing = Get-CanvasPaged -Url "$base/api/v1/courses/$cid/quizzes/$($cs.quiz.id)/questions?per_page=100" -Headers $hdr

        Write-Host ("       questions: delete {0}, create {1}" -f $existing.Count, $quizQuestions.Count)
        if ($quizDesc) { Write-Host ("       description {0} -> {1} chars" -f $cur.description.Length, $quizDesc.Length) }
        if ($cs.quiz.expected_question_count -and $quizQuestions.Count -ne $cs.quiz.expected_question_count) {
            throw ("Changeset says expected_question_count={0} but questions_file has {1} - refusing (this would change the quiz's point total)." -f $cs.quiz.expected_question_count, $quizQuestions.Count)
        }
        foreach ($q in $quizQuestions) {
            $correct = @($q.answers | Where-Object { $_[1] -gt 0 })
            if ($correct.Count -ne 1) { throw "Question '$($q.name)' has $($correct.Count) correct answer(s), must be exactly 1." }
        }
        Write-Host "       answer-key audit: every question has exactly one correct answer - OK"

        if ($requireUnpub) {
            $course = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid" -Headers $hdr
            Write-Host ("       safety: course workflow_state = '{0}'" -f $course.workflow_state)
            if ($course.workflow_state -ne 'unpublished') {
                throw "Course is not unpublished - rebuilding questions could destroy real attempt data. Set require_unpublished_course: false in the changeset only if you have verified independently that no attempts exist."
            }
        }
    } elseif ($quizDesc) {
        Write-Host ("       description {0} -> {1} chars" -f $cur.description.Length, $quizDesc.Length)
    }
    $plan += @{ kind = 'quiz'; id = $cs.quiz.id; description = $quizDesc; questions = $quizQuestions }
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "DRY RUN - no changes written. Re-run with -Apply."
    exit 0
}

Write-Host ""
foreach ($step in $plan) {
    switch ($step.kind) {
        'page' {
            $r = Send-CanvasJson -Method Put -Url "$base/api/v1/courses/$cid/pages/$($step.slug)" -Headers $hdr `
                -Body @{ wiki_page = @{ body = $step.body } }
            Write-Host ("  OK  page '{0}' (published={1})" -f $r.title, $r.published)
        }
        'assignment' {
            $r = Send-CanvasJson -Method Put -Url "$base/api/v1/courses/$cid/assignments/$($step.id)" -Headers $hdr `
                -Body @{ assignment = @{ description = $step.description } }
            Write-Host ("  OK  assignment '{0}' ({1} pts)" -f $r.name, $r.points_possible)
        }
        'quiz' {
            if ($step.description) {
                $null = Send-CanvasJson -Method Put -Url "$base/api/v1/courses/$cid/quizzes/$($step.id)" -Headers $hdr `
                    -Body @{ quiz = @{ description = $step.description; notify_of_update = $false } }
                Write-Host "  OK  quiz description updated"
            }
            if ($step.questions) {
                $existing = Get-CanvasPaged -Url "$base/api/v1/courses/$cid/quizzes/$($step.id)/questions?per_page=100" -Headers $hdr
                foreach ($q in $existing) {
                    Invoke-RestMethod -Method Delete -Uri "$base/api/v1/courses/$cid/quizzes/$($step.id)/questions/$($q.id)" -Headers $hdr | Out-Null
                }
                Write-Host ("  ..  deleted {0} old question(s)" -f $existing.Count)
                $pos = 0
                foreach ($q in $step.questions) {
                    $pos++
                    $answers = @()
                    foreach ($a in $q.answers) { $answers += @{ answer_text = [string]$a[0]; answer_weight = [int]$a[1] } }
                    $null = Send-CanvasJson -Method Post -Url "$base/api/v1/courses/$cid/quizzes/$($step.id)/questions" -Headers $hdr -Body @{
                        question = @{
                            question_name = $q.name; question_text = $q.text; question_type = 'multiple_choice_question'
                            points_possible = 1; position = $pos; incorrect_comments = $q.incorrect; answers = $answers
                        }
                    }
                    Write-Host ("      created Q{0,-2} {1}" -f $pos, $q.name)
                }
                $null = Send-CanvasJson -Method Put -Url "$base/api/v1/courses/$cid/quizzes/$($step.id)" -Headers $hdr `
                    -Body @{ quiz = @{ notify_of_update = $false } }
            }
        }
    }
}
Write-Host ""
Write-Host "APPLY complete."
