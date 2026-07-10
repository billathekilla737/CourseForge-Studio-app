<#
  Push-CanvasProject.ps1
  Build a "project / capstone" style Canvas course from a project manifest:
  a front-page Home, a Syllabus tab, content/template pages, upload assignments,
  graded discussions, and weekly Modules whose items can be Pages, Assignments,
  Discussions, or SubHeaders.

  This is the generalized form of the one-off capstone scripts. It is idempotent:
  pages upsert by slug, assignments by name, discussions by title; modules are
  rebuilt from scratch each run (cleared then recreated) so item order always
  matches the manifest.

  WHY A SEPARATE SCRIPT FROM Push-CanvasPages.ps1: lesson courses are pages-only
  and place items incrementally with state. Project courses add assignments +
  graded discussions + a front page + a syllabus tab, and mix item types in a
  module. Keeping them separate keeps each path simple.

  ENCODING (Gotcha 1): this file is ASCII. Every em dash and accented character
  lives in the *manifest JSON* (read as UTF-8), never in a string literal here, so
  PowerShell 5.1 reading the .ps1 as ANSI cannot mangle a title. That is what
  removed the need for the old "fix titles" remediation pass.

  Usage:
    .\Push-CanvasProject.ps1 -ConfigPath .\canvas.config.12345.json `
        -ManifestPath .\canvas-export\project.12345.json `
        -StatePath .\canvas.project.12345.json
    .\Push-CanvasProject.ps1 ... -WhatIf        # plan only, no writes
    .\Push-CanvasProject.ps1 ... -SkipModules   # content pass only
#>
param(
    [string]$Root         = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$ManifestPath,
    [string]$TokenPath    = '',   # default: canvas.token next to the config (CanvasContext.ps1)
    [string]$StatePath    = (Join-Path $PSScriptRoot '..\canvas.project.state.json'),
    [ValidateSet('published','unpublished')] [string]$PublishState = 'unpublished',
    [switch]$SkipModules,
    [switch]$RebuildModules,   # required to wipe modules on a course this script did not build
    [switch]$WhatIf
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

. "$PSScriptRoot\CanvasContext.ps1"
$ctx = Resolve-CanvasContext -ConfigPath $ConfigPath -TokenPath $TokenPath
$ConfigPath = $ctx.ConfigPath; $TokenPath = $ctx.TokenPath

$cfg      = Get-Content -Raw -Encoding UTF8 $ConfigPath   | ConvertFrom-Json
$manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
$token    = (Get-Content -Raw $TokenPath).Trim()
$base     = $cfg.base_url.TrimEnd('/')
$courseId = $cfg.course_id
$api      = "$base/api/v1/courses/$courseId"
$headers  = @{ Authorization = "Bearer $token" }

# Publish state for everything this run creates/updates (Canvas calls it
# "published"). Default 'unpublished' keeps content hidden from students until the
# instructor is ready; the skill asks before each push and passes -PublishState.
$pub = if ($PublishState -eq 'published') { 'true' } else { 'false' }

# Form-encoded request (UTF-8 bytes). Used for pages, assignments, discussions,
# course settings, and module *create* -- all of which accept form bodies.
function Invoke-Canvas {
    param([string]$Method, [string]$Path, [hashtable]$Body)
    $uri = "$api$Path"
    try {
        if ($Body) {
            $pairs = foreach ($k in $Body.Keys) { '{0}={1}' -f [uri]::EscapeDataString($k), [uri]::EscapeDataString([string]$Body[$k]) }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
            return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -Body $bytes -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -ErrorAction Stop
        }
        return Invoke-RestMethod -Uri $uri -Headers $headers -Method $Method -ErrorAction Stop
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.Response) {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $msg = "$msg :: " + $sr.ReadToEnd()
        }
        throw "Canvas $Method $uri failed: $msg"
    }
}

# Module ITEM adds must be JSON (Gotcha 3): form bodies 400 for Assignment /
# Discussion / SubHeader and are unreliable for Page.
function Add-ModuleItem {
    param([int]$ModuleId, [hashtable]$Item)
    $json  = (@{ module_item = $Item } | ConvertTo-Json -Compress)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri "$api/modules/$ModuleId/items" -Headers $headers -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -ErrorAction Stop
}

function Body-File {
    param([string]$RelOrAbs)
    $full = if ([IO.Path]::IsPathRooted($RelOrAbs)) { $RelOrAbs } else { Join-Path $Root $RelOrAbs }
    if (-not (Test-Path $full)) { throw "missing body file: $full" }
    return (Get-Content -Raw -Encoding UTF8 $full)
}

# Generic JSON request (UTF-8 bytes). Used for quiz questions (nested answer
# arrays are painful as form bodies) and anything else that prefers JSON.
function Invoke-CanvasJson {
    param([string]$Method, [string]$Path, $Obj)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Obj | ConvertTo-Json -Depth 12 -Compress))
    return Invoke-RestMethod -Uri "$api$Path" -Headers $headers -Method $Method -Body $bytes -ContentType 'application/json; charset=utf-8' -ErrorAction Stop
}

# Assignment groups organize the gradebook. Resolve a group name to its id,
# creating the group if it does not exist. Cached for the run.
$agCache = @{}
function Get-AssignmentGroupId {
    param([string]$Name)
    if (-not $Name) { return $null }
    if ($agCache.ContainsKey($Name)) { return $agCache[$Name] }
    $grp = Invoke-Canvas POST "/assignment_groups" @{ 'name'=$Name }
    $agCache[$Name] = $grp.id
    return $grp.id
}

$state = [ordered]@{ pages=@{}; assignments=@{}; discussions=@{}; quizzes=@{}; modules=@() }

# --- SAFETY GATE (before ANY write): module wipe protection ------------------
# The module pass (section 5) deletes every existing module and rebuilds from the
# manifest. That is correct for a shell this script owns, and DESTRUCTIVE for a
# populated course it has never seen. Refuse up front - before content writes -
# unless (a) the course has no modules, (b) a prior state file proves this script
# built it, or (c) the operator explicitly passed -RebuildModules.
if (-not $WhatIf -and -not $SkipModules) {
    # assign-then-wrap: @(Invoke-Canvas ...) directly would nest the array on
    # PS 5.1 and report Count=1 (gate still fires, but the message would lie)
    $existingMods = Invoke-Canvas GET "/modules?per_page=100"
    $existingMods = @($existingMods)
    $ownCourse    = Test-Path $StatePath
    if ($existingMods.Count -gt 0 -and -not $ownCourse -and -not $RebuildModules) {
        Write-Host ""
        Write-Host "REFUSING: course $courseId already has $($existingMods.Count) module(s) and no prior state file"
        Write-Host "($StatePath) shows this script built them. Rebuilding would DELETE the"
        Write-Host "instructor's existing module structure. Nothing has been written."
        Write-Host "  - Update content without touching modules:  re-run with -SkipModules"
        Write-Host "  - Wipe and rebuild modules (destructive):   re-run with -RebuildModules"
        exit 2
    }
}

Write-Host "Target: $($manifest.course_label)  ($api)"
Write-Host "Publish state: $PublishState"
if ($WhatIf) { Write-Host "[WhatIf] planning only, no writes`n" } else { Write-Host "" }

# --- 1) Pages (PUT by slug = upsert) --------------------------------------
foreach ($pg in $manifest.pages) {
    if ($WhatIf) { Write-Host "  [WhatIf] page '$($pg.title)' (/$($pg.slug))"; continue }
    $b = @{ 'wiki_page[title]'=$pg.title; 'wiki_page[body]'=(Body-File $pg.file); 'wiki_page[published]'=$pub }
    if ($pg.front_page) { $b['wiki_page[front_page]'] = 'true' }
    $r = Invoke-Canvas PUT "/pages/$($pg.slug)" $b
    $state.pages[$pg.slug] = $r.url
    Write-Host "  page: /$($r.url)"
}

# Front page + landing view
if (-not $WhatIf -and ($manifest.pages | Where-Object { $_.front_page })) {
    Invoke-Canvas PUT "" @{ 'course[default_view]'='wiki' } | Out-Null
    Write-Host "  default_view = wiki (front page is the course landing)"
}

# --- 2) Syllabus tab -------------------------------------------------------
if ($manifest.syllabus_file) {
    if ($WhatIf) { Write-Host "  [WhatIf] syllabus tab <- $($manifest.syllabus_file)" }
    else { Invoke-Canvas PUT "" @{ 'course[syllabus_body]'=(Body-File $manifest.syllabus_file) } | Out-Null; Write-Host "  syllabus tab set" }
}

# --- 3) Assignments (upsert by name) --------------------------------------
# Prime the assignment-group cache so existing groups are reused, not duplicated.
if (-not $WhatIf) { foreach ($g in (Invoke-Canvas GET "/assignment_groups?per_page=100")) { $agCache[$g.name] = $g.id } }
$existingA = if ($WhatIf) { @() } else { Invoke-Canvas GET "/assignments?per_page=100" }
foreach ($a in $manifest.assignments) {
    if ($WhatIf) { Write-Host "  [WhatIf] assignment '$($a.name)' ($($a.points) pts)"; continue }
    $b = @{ 'assignment[name]'=$a.name; 'assignment[description]'=(Body-File $a.file);
            'assignment[points_possible]'=[string]$a.points; 'assignment[published]'=$pub }
    if ($a.due_at) { $b['assignment[due_at]'] = $a.due_at }
    if ($a.group)  { $b['assignment[assignment_group_id]'] = [string](Get-AssignmentGroupId $a.group) }
    foreach ($st in @($a.submission_types)) { if ($st) { $b['assignment[submission_types][]'] = $st } }
    $hit = $existingA | Where-Object { $_.name -eq $a.name } | Select-Object -First 1
    $r = if ($hit) { Invoke-Canvas PUT "/assignments/$($hit.id)" $b } else { Invoke-Canvas POST "/assignments" $b }
    $state.assignments[$a.key] = $r.id
    Write-Host "  assignment '$($a.name)' id=$($r.id)"
}

# --- 4) Graded discussions (upsert by title) ------------------------------
# A graded discussion = discussion_topics + an assignment[...] block (Gotcha 5).
$existingD = if ($WhatIf) { @() } else { Invoke-Canvas GET "/discussion_topics?per_page=100" }
foreach ($d in $manifest.discussions) {
    if ($WhatIf) { Write-Host "  [WhatIf] discussion '$($d.title)' ($($d.points) pts)"; continue }
    $msg = (Body-File $d.file)
    if ($d.note) {
        $box = '<div style="padding:10px 12px;border-radius:8px;background:#fff8e6;border-left:4px solid #E9A821;font-size:14px;color:#061E3F;margin-bottom:12px;"><strong>' +
               $(if ($d.note_label) { $d.note_label } else { "This week's milestone:" }) + '</strong> ' + $d.note + '</div>'
        $msg = $box + $msg
    }
    $b = @{ 'title'=$d.title; 'message'=$msg; 'published'=$pub;
            'assignment[points_possible]'=[string]$d.points; 'assignment[grading_type]'='points';
            'assignment[submission_types][]'='discussion_topic' }
    if ($d.due_at) { $b['assignment[due_at]'] = $d.due_at }
    $hit = $existingD | Where-Object { $_.title -eq $d.title } | Select-Object -First 1
    $r = if ($hit) { Invoke-Canvas PUT "/discussion_topics/$($hit.id)" $b } else { Invoke-Canvas POST "/discussion_topics" $b }
    $state.discussions[$d.key] = $r.id
    Write-Host "  discussion '$($d.title)' id=$($r.id)"
}

# --- 4.5) Quizzes (Classic Quizzes; upsert by title) ----------------------
# Create the quiz unpublished, (re)build its question bank, then publish. On a
# re-run we delete the old questions first so they do not accumulate. Question
# bodies go as JSON (nested answer arrays). content_id for the module item is
# the quiz id.
$existingQ = if ($WhatIf) { @() } else { Invoke-Canvas GET "/quizzes?per_page=100" }
foreach ($q in $manifest.quizzes) {
    if ($WhatIf) { Write-Host "  [WhatIf] quiz '$($q.title)' ($(@($q.questions).Count) questions)"; continue }
    $desc  = if ($q.file) { Body-File $q.file } else { '' }
    $qbody = @{ 'quiz[title]'=$q.title; 'quiz[description]'=$desc; 'quiz[quiz_type]'='assignment'; 'quiz[published]'='false' }
    if ($q.time_limit)      { $qbody['quiz[time_limit]']      = [string]$q.time_limit }
    if ($q.shuffle_answers) { $qbody['quiz[shuffle_answers]'] = 'true' }
    if ($q.due_at)          { $qbody['quiz[due_at]']          = $q.due_at }
    if ($q.group)           { $qbody['quiz[assignment_group_id]'] = [string](Get-AssignmentGroupId $q.group) }
    $hit = $existingQ | Where-Object { $_.title -eq $q.title } | Select-Object -First 1
    if ($hit) {
        $r = Invoke-Canvas PUT "/quizzes/$($hit.id)" $qbody
        foreach ($oldq in (Invoke-Canvas GET "/quizzes/$($hit.id)/questions?per_page=100")) { Invoke-Canvas DELETE "/quizzes/$($hit.id)/questions/$($oldq.id)" | Out-Null }
    } else {
        $r = Invoke-Canvas POST "/quizzes" $qbody
    }
    $qid = $r.id
    $n = 0
    foreach ($qq in $q.questions) {
        $n++
        $ans = @()
        foreach ($a in @($qq.answers)) { $ans += @{ answer_text = [string]$a.text; answer_weight = $(if ($a.correct) { 100 } else { 0 }) } }
        $question = @{ question_name = ("Question {0}" -f $n); question_text = $qq.text; question_type = $qq.type; points_possible = $qq.points }
        if ($qq.type -ne 'essay_question' -and $ans.Count) { $question['answers'] = @($ans) }
        Invoke-CanvasJson POST "/quizzes/$qid/questions" @{ question = $question } | Out-Null
    }
    Invoke-Canvas PUT "/quizzes/$qid" @{ 'quiz[published]'=$pub } | Out-Null
    $state.quizzes[$q.key] = $qid
    Write-Host "  quiz '$($q.title)' id=$qid ($(@($q.questions).Count) questions)"
}

# --- 5) Modules (rebuild from scratch) ------------------------------------
if ($SkipModules) { Write-Host "`n-SkipModules: leaving modules untouched." }
elseif ($WhatIf)  { foreach ($m in $manifest.modules) { Write-Host "  [WhatIf] module '$($m.name)' ($(@($m.items).Count) items)" } }
else {
    foreach ($m in (Invoke-Canvas GET "/modules?per_page=100")) { Invoke-Canvas DELETE "/modules/$($m.id)" | Out-Null }
    Write-Host "  cleared existing modules"
    $pos = 1
    foreach ($m in $manifest.modules) {
        $mod = Invoke-Canvas POST "/modules" @{ 'module[name]'=$m.name; 'module[position]'=[string]$pos }
        Invoke-Canvas PUT "/modules/$($mod.id)" @{ 'module[published]'=$pub } | Out-Null
        $pos++
        foreach ($it in $m.items) {
            switch ($it.type) {
                'Page'       { Add-ModuleItem -ModuleId $mod.id -Item @{ type='Page';       page_url=$state.pages[$it.slug] } | Out-Null }
                'Assignment' { Add-ModuleItem -ModuleId $mod.id -Item @{ type='Assignment'; content_id=[int]$state.assignments[$it.key] } | Out-Null }
                'Discussion' { Add-ModuleItem -ModuleId $mod.id -Item @{ type='Discussion'; content_id=[int]$state.discussions[$it.key] } | Out-Null }
                'Quiz'       { Add-ModuleItem -ModuleId $mod.id -Item @{ type='Quiz';       content_id=[int]$state.quizzes[$it.key] } | Out-Null }
                'SubHeader'  { Add-ModuleItem -ModuleId $mod.id -Item @{ type='SubHeader';  title=$it.title } | Out-Null }
                default      { Write-Host "    ! unknown item type '$($it.type)' in module '$($m.name)'" }
            }
        }
        $state.modules += $m.name
        Write-Host "  + module '$($m.name)' ($(@($m.items).Count) items)"
    }
}

if (-not $WhatIf) {
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding utf8
    Write-Host "`nDone. State -> $StatePath"
    Write-Host ("Review: {0}/courses/{1}" -f $base, $courseId)
}
