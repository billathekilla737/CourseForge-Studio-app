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
#   1. Block reads of local student-data caches (private/, grading/, audit log).
#   2. If the text does not reference the Canvas API at all -> allow (not our concern).
#   3. Allow the one sanctioned sterilizing gateway script by name.
#   4. Deny any Canvas student-data (PII) endpoint.
#   5. Allow known Canvas CONTENT endpoints.
#   6. Otherwise (a Canvas API call we do not recognize) -> DENY (fail-closed).
function Test-CanvasCallAllowed {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return @{ allowed = $true; reason = '' } }
    $low = $Text.ToLower()

    # 1. local student-data caches
    if ($low -match 'private[\\/]' -or $low -match 'grading[\\/]' -or
        $low -match 'submissions\.index\.json' -or $low -match 'canvas-admin-audit\.log' -or
        $low -match 'proposed-grades') {
        return @{ allowed = $false; reason = 'canvas-pii-guard: reading a local student-data cache (private/ or grading/) is blocked.' }
    }

    # 2. only govern Canvas API references
    if (-not (($low -match '/api/v1/') -or ($low -match 'instructure\.com'))) {
        return @{ allowed = $true; reason = '' }
    }

    # 3. sanctioned sterilizing gateway (short-circuits; the gateway sterilizes its own output)
    if ($low -match 'get-canvasdata-sterilized\.ps1') { return @{ allowed = $true; reason = '' } }

    # 4. student-data (PII) endpoints -> DENY (segment-anchored; checked before content allow)
    $denyRx = '/(submissions|submission_summary|gradebook|grades|grade_change|enrollments|users|students|observees|observers|analytics|conversations|entries|entry_list|activity_stream|sis_imports|logins|profile|avatars|feeds|recent_students|student_view|effective_due_dates|quiz_submissions)([/?]|$|[^a-z0-9_])'
    if ($low -match $denyRx) {
        return @{ allowed = $false; reason = 'canvas-pii-guard: Canvas student-data endpoint is not permitted from this skill. If data is genuinely needed, route it through Get-CanvasData-Sterilized.ps1.' }
    }

    # 5. known CONTENT endpoints -> allow (segment-anchored)
    $allowRx = '/(pages|modules|module_items|assignments|assignment_groups|quizzes|discussion_topics|tabs|files|folders|external_tools)([/?]|$|[^a-z0-9_])'
    if ($low -match $allowRx) { return @{ allowed = $true; reason = '' } }
    # bare course settings: /courses/<id> NOT followed by another path segment
    if ($low -match '/courses/\d+(?![\d/])') { return @{ allowed = $true; reason = '' } }

    # 6. fail-closed: a Canvas API call we do not recognize
    return @{ allowed = $false; reason = 'canvas-pii-guard (fail-closed): unrecognized Canvas API endpoint. Only content endpoints are allowed from this skill.' }
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
    [void]$patterns.Add(@{ rx = '\b\d{9}\b'; rep = '[ID9]' })
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
