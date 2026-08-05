<#
  Get-CanvasModule.ps1 - READ-ONLY dump of one Canvas module: every item, every
  instructor-authored HTML body (page/assignment/quiz description), and every quiz
  question with its current answer key. Content endpoints only - never touches
  rosters, grades, or submissions.

  Usage:
    pwsh -File Get-CanvasModule.ps1 -ModuleId 5106504 -WorkDir ./work/m7
    pwsh -File Get-CanvasModule.ps1 -ModuleId 5106504 -CourseId 734709 -WorkDir ./work/m7

  Output (under -WorkDir):
    module.json            raw module + items as Canvas returned them
    ids.json                {page_slug, assignment_id, quiz_id, ...} - paste straight
                            into a changeset for Push-CanvasModule.ps1
    bodies/<NN>_<Type>_<Title>.html     one file per restylable HTML body
    quiz/<quizId>.questions.json         current questions in the same schema
                            Push-CanvasModule.ps1 expects for -QuestionsFile
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ModuleId,
    [string]$CourseId,
    [string]$ConfigPath,
    [string]$TokenPath,
    [Parameter(Mandatory)][string]$WorkDir
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib_canvas.ps1')

$ctx = Resolve-CanvasConfig -ConfigPath $ConfigPath -TokenPath $TokenPath -CourseId $CourseId
$base = $ctx.BaseUrl; $cid = $ctx.CourseId; $hdr = $ctx.Headers

New-Item -ItemType Directory -Force -Path $WorkDir, (Join-Path $WorkDir 'bodies'), (Join-Path $WorkDir 'quiz') | Out-Null

$module = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/modules/$ModuleId" -Headers $hdr
$items  = Get-CanvasPaged -Url "$base/api/v1/courses/$cid/modules/$ModuleId/items?per_page=100" -Headers $hdr

Write-Host ("Module '{0}' (id {1}, {2} items, published={3})" -f $module.name, $module.id, $items.Count, $module.published)

$ids = [ordered]@{ course_id = $cid; base_url = $base; module_id = "$ModuleId" }
$inventory = @()

foreach ($it in ($items | Sort-Object position)) {
    $body = $null; $meta = [ordered]@{}
    try {
        switch ($it.type) {
            'Page' {
                $p = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/pages/$($it.page_url)" -Headers $hdr
                $body = $p.body; $meta.slug = $p.url; $meta.updated_at = $p.updated_at
                $ids.page_slug = $p.url
            }
            'Assignment' {
                $a = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/assignments/$($it.content_id)" -Headers $hdr
                $body = $a.description
                $meta.points = $a.points_possible; $meta.due_at = $a.due_at
                $meta.submission_types = ($a.submission_types -join ',')
                if (-not $ids.Contains('assignment_id')) { $ids.assignment_id = "$($a.id)" }
                else { $ids["assignment_id_$($a.id)"] = "$($a.id)" }
            }
            'Quiz' {
                $q = Invoke-CanvasGet -Url "$base/api/v1/courses/$cid/quizzes/$($it.content_id)" -Headers $hdr
                $body = $q.description
                $meta.points = $q.points_possible; $meta.question_count = $q.question_count
                $meta.quiz_type = $q.quiz_type; $meta.time_limit = $q.time_limit
                $ids.quiz_id = "$($q.id)"

                $qs = Get-CanvasPaged -Url "$base/api/v1/courses/$cid/quizzes/$($q.id)/questions?per_page=100" -Headers $hdr
                $exported = @()
                foreach ($x in $qs) {
                    $answers = @()
                    foreach ($a in $x.answers) { $answers += , @($a.text, [int]$a.weight) }
                    $exported += [ordered]@{
                        name      = $x.question_name
                        text      = $x.question_text
                        incorrect = $x.incorrect_comments
                        answers   = $answers
                    }
                }
                $qFile = Join-Path $WorkDir "quiz/$($q.id).questions.json"
                [IO.File]::WriteAllText($qFile, ($exported | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
                Write-Host ("  dumped {0} quiz question(s) -> {1}" -f $exported.Count, $qFile)
            }
            'ExternalTool' { $meta.external_url = $it.external_url }
            'File' { $meta.content_id = $it.content_id }
        }
    } catch { $meta.fetch_error = $_.Exception.Message }

    $hasBody = ($body -and $body.Length -gt 0)
    Write-Host ("  pos {0,2} | {1,-12} | {2,-46} | {3}" -f $it.position, $it.type, `
        $it.title.Substring(0, [Math]::Min(46, $it.title.Length)), `
        $(if ($hasBody) { "$($body.Length) chars, restylable" } else { "no HTML body" }))

    if ($hasBody) {
        $safe = ($it.title -replace '[^A-Za-z0-9._ -]', '_') -replace '\s+', '_'
        $file = Join-Path $WorkDir ("bodies/{0:d2}_{1}_{2}.html" -f $it.position, $it.type, $safe)
        [IO.File]::WriteAllText($file, $body, (New-Object Text.UTF8Encoding($false)))
        $meta.body_file = $file
    }

    $inventory += [ordered]@{
        position = $it.position; type = $it.type; title = $it.title
        module_item_id = $it.id; content_id = $it.content_id; page_url = $it.page_url
        published = $it.published; meta = $meta
    }
}

[IO.File]::WriteAllText((Join-Path $WorkDir 'module.json'),
    (@{ module = $module; items = $inventory } | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $WorkDir 'ids.json'),
    ($ids | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "ids.json (paste into your changeset):"
Get-Content (Join-Path $WorkDir 'ids.json')
