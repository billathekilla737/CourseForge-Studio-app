<#
  PiiPatterns.ps1  (canvas-pii-guard) - shared library, dot-source this.
  Single source of truth for: (1) the Canvas endpoint allow/deny policy used by the
  PreToolUse block hook, and (2) the PII redaction patterns used by the redactor and
  the sterilizing gateway. The test harness exercises THESE functions directly, so the
  evidence report reflects exactly what runs in production.

  ASCII only. No secrets. No network. Pure functions.
#>

# --- Policy: is this tool call allowed to reach Canvas? -----------------------
# Returns a hashtable @{ allowed = $true/$false; reason = '...' }.
# Logic order (fail-closed for Canvas data endpoints):
#   0. Allow the SANCTIONED gateway scripts by name (sterilizing / pseudonymizing).
#      Checked FIRST because they legitimately touch /submissions AND read the local
#      grading\ map; they sterilize / tokenize their own output.
#   1. Block reads of local student-data caches (private/, grading/, audit log).
#   2. Isolate the ACTUAL Canvas URL tokens (a whitespace-delimited token containing
#      '/api/v1/' or 'instructure.com/<path>'). If there are none -> allow (not our
#      concern). Evaluating only these tokens -- not the whole command blob -- avoids
#      false positives where a sensitive word merely co-occurs in unrelated text
#      (a Windows C:/Users/ path, a git commit message, prose, a tool description).
#   3. For each real Canvas URL token: DENY if it hits a student-data (PII) segment.
#   4. Then each real Canvas URL token must be a recognized CONTENT or course
#      settings/list endpoint, else DENY (fail-closed).
function Test-CanvasCallAllowed {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return @{ allowed = $true; reason = '' } }
    $low = $Text.ToLower()

    # 0. sanctioned gateway scripts (short-circuit BEFORE the local-cache block and the
    #    submissions deny). Each sterilizes/pseudonymizes its own output; raw identities
    #    stay local. Any OTHER command hitting the same endpoints/caches stays blocked.
    if ($low -match 'get-canvasdata-sterilized\.ps1' -or
        $low -match 'build-gradingbundle\.ps1' -or
        $low -match 'post-grades\.ps1') {
        return @{ allowed = $true; reason = '' }
    }

    # 1. local student-data caches (whole-text: these are file paths, not URLs)
    if ($low -match 'private[\\/]' -or $low -match 'grading[\\/]' -or
        $low -match 'submissions\.index\.json' -or $low -match 'canvas-admin-audit\.log' -or
        $low -match 'proposed-grades') {
        return @{ allowed = $false; reason = 'canvas-pii-guard: reading a local student-data cache (private/ or grading/) is blocked.' }
    }

    # 2. isolate the real Canvas URL tokens. A token qualifies only if it is an actual
    #    Canvas path/URL ('/api/v1/...' or 'instructure.com/...') -- NOT a bare host, an
    #    email at *.instructure.com, or an unrelated /Users path. No Canvas URL -> allow.
    $denyRx  = '/(submissions|submission_summary|gradebook|grades|grade_change|enrollments|users|students|observees|observers|analytics|conversations|entries|entry_list|activity_stream|sis_imports|logins|profile|avatars|feeds|recent_students|student_view|effective_due_dates|quiz_submissions)([/?]|$|[^a-z0-9_])'
    # content_exports/content_migrations/progress are content-plane (course copy /
    # common-cartridge / async status poll); deny runs FIRST (step 3), so adding them
    # here cannot open any student-data endpoint. The [^a-z0-9_] boundary keeps
    # look-alikes (e.g. /progress_reports) fail-closed.
    $allowRx = '/(pages|modules|module_items|assignments|assignment_groups|quizzes|discussion_topics|tabs|files|folders|external_tools|content_exports|content_migrations|progress)([/?]|$|[^a-z0-9_])'

    $canvasTokens = @(($low -split '\s+') | Where-Object { $_ -match '/api/v1/' -or $_ -match 'instructure\.com/' })
    if ($canvasTokens.Count -eq 0) { return @{ allowed = $true; reason = '' } }

    # 3. deny any real Canvas URL that targets a student-data (PII) segment
    foreach ($tok in $canvasTokens) {
        if ($tok -match $denyRx) {
            return @{ allowed = $false; reason = 'canvas-pii-guard: Canvas student-data endpoint is not permitted from this skill. If data is genuinely needed, route it through a sanctioned gateway (Get-CanvasData-Sterilized.ps1, or for blind grading Build-GradingBundle.ps1 / Post-Grades.ps1).' }
        }
    }

    # 4. each real Canvas URL must be a recognized CONTENT endpoint, a single course
    #    (/courses/<id>), or the course LIST (/courses) -- otherwise fail-closed.
    foreach ($tok in $canvasTokens) {
        $ok = ($tok -match $allowRx) -or
              ($tok -match '/courses/\d+(?![\d/])') -or
              ($tok -match '/courses([?]|$|[^a-z0-9_/])')
        if (-not $ok) {
            return @{ allowed = $false; reason = 'canvas-pii-guard (fail-closed): unrecognized Canvas API endpoint. Only content endpoints are allowed from this skill.' }
        }
    }

    return @{ allowed = $true; reason = '' }
}

# --- Redaction: scrub structured PII from text --------------------------------
# Profile 'Standard' is conservative (safe for general tool output: does NOT touch a
# bare "name" field, so content names like assignment/course names survive).
# Profile 'Strict' also redacts name-like fields (use for known user/roster data).
# Returns [pscustomobject] @{ Text = <redacted>; Count = <n> }.
function Invoke-PiiRedaction {
    param([string]$Text, [ValidateSet('Standard','Strict')] [string]$Profile = 'Standard')
    if ([string]::IsNullOrEmpty($Text)) { return [pscustomobject]@{ Text = $Text; Count = 0 } }
    $s = $Text
    $count = 0

    $patterns = New-Object System.Collections.ArrayList
    [void]$patterns.Add(@{ rx = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'; rep = '[EMAIL]' })
    [void]$patterns.Add(@{ rx = '\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'; rep = '[PHONE]' })
    # MGCCC-specific ID formats (learned from a de-identified roster sample; longer/more
    # specific patterns first so the whole token is redacted before sub-patterns match).
    # CONFIRMED real format = letter + 8 digits. The {8,9} range is a deliberate redaction
    # safety margin (still matches the real 8-digit IDs; also catches a stray longer one).
    # For exact precision instead, change {8,9} to {8}.
    [void]$patterns.Add(@{ rx = '\b\d{3}\.[A-Za-z]\d{8,9}\b'; rep = '[SISID]' })  # sis_user_id: ###.X######## (8 digits)
    [void]$patterns.Add(@{ rx = '\b[A-Za-z]\d{8,9}\b'; rep = '[USERID]' })         # login_id: letter + 8 digits
    # SSN shape only. (A bare \b\d{9}\b rule was removed: it false-positived on Canvas
    # file/course/export/progress ids, breaking id-carrying content workflows. Real
    # MGCCC student ids are covered by the letter+8 patterns above; roster JSON by the
    # field pattern below.)
    [void]$patterns.Add(@{ rx = '\b\d{3}-\d{2}-\d{4}\b'; rep = '[SSN]' })
    [void]$patterns.Add(@{ rx = '(/users/)\d+'; rep = '${1}[ID]' })
    [void]$patterns.Add(@{ rx = '(/submissions/)\d+'; rep = '${1}[ID]' })
    [void]$patterns.Add(@{ rx = '(/enrollments/)\d+'; rep = '${1}[ID]' })
    [void]$patterns.Add(@{ rx = '([?&]user_id=)\d+'; rep = '${1}[ID]' })
    [void]$patterns.Add(@{ rx = '("(?:sortable_name|short_name|login_id|sis_user_id|integration_id|email|pronouns)"\s*:\s*")[^"]*(")'; rep = '${1}[REDACTED]${2}' })
    if ($Profile -eq 'Strict') {
        [void]$patterns.Add(@{ rx = '("(?:name|first_name|last_name|display_name|user_name)"\s*:\s*")[^"]*(")'; rep = '${1}[REDACTED]${2}' })
    }

    foreach ($p in $patterns) {
        $m = [regex]::Matches($s, $p.rx)
        if ($m.Count -gt 0) {
            $count += $m.Count
            $s = [regex]::Replace($s, $p.rx, $p.rep)
        }
    }
    return [pscustomobject]@{ Text = $s; Count = $count }
}

function Test-PiiPresent {
    param([string]$Text, [ValidateSet('Standard','Strict')] [string]$Profile = 'Standard')
    (Invoke-PiiRedaction -Text $Text -Profile $Profile).Count -gt 0
}
