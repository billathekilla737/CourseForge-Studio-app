<#
  Run-GuardTests.ps1  (canvas-pii-guard) - evidence harness.
  Exercises the SAME functions the hooks use (Test-CanvasCallAllowed, Invoke-PiiRedaction)
  against a synthetic, de-identified PII corpus. Proves: PII endpoints are blocked,
  content endpoints pass, and structured PII is redacted - WITHOUT over-redacting course
  content. Writes guard-coverage-report.txt. Exit code = number of failures.

  NOTE: synthetic data only (no real students). When MGCCC sample data arrives, add its
  patterns here and re-run to extend the proof. Passing these tests demonstrates coverage
  of the listed structured-PII forms; it does NOT certify catch-all coverage of arbitrary
  free-text PII (see DATA-HANDLING.md: the guarantee is the BLOCK/prevention layer).
#>
. "$PSScriptRoot\..\scripts\PiiPatterns.ps1"

$pass = 0; $fail = 0; $lines = @()
function Check([bool]$cond, [string]$label) {
    if ($cond) { $script:pass++; $script:lines += ("  PASS  " + $label) }
    else       { $script:fail++; $script:lines += ("  FAIL  " + $label) }
}

$B = 'https://mgccc.instructure.com/api/v1/courses/1'

$lines += "== POLICY (block hook) =="
$policy = @(
    @{ t = "Invoke-RestMethod $B/pages";                         e = $true;  l = 'allow: /pages' },
    @{ t = "Invoke-RestMethod $B/assignments?per_page=100";      e = $true;  l = 'allow: /assignments (definition)' },
    @{ t = "Invoke-RestMethod $B/quizzes/9/questions";           e = $true;  l = 'allow: /quizzes/:id/questions' },
    @{ t = "Invoke-RestMethod $B/modules";                       e = $true;  l = 'allow: /modules' },
    @{ t = "Invoke-RestMethod $B/discussion_topics";             e = $true;  l = 'allow: /discussion_topics' },
    @{ t = "Invoke-RestMethod ""$B`?include[]=total_students"""; e = $true;  l = 'allow: course settings + total_students' },
    @{ t = 'git commit -m "build pages"';                        e = $true;  l = 'allow: non-Canvas command' },
    @{ t = "powershell -File Get-CanvasData-Sterilized.ps1 -Path /users?per_page=100"; e = $true; l = 'allow: sanctioned gateway by name' },
    @{ t = "powershell -File Build-GradingBundle.ps1 -ConfigPath ..\canvas.config.1.json -AssignmentId 67890 # GET $B/assignments/67890/submissions?include[]=user"; e = $true; l = 'allow: Build-GradingBundle.ps1 (sanctioned) hitting /submissions' },
    @{ t = "powershell -File Post-Grades.ps1 -ConfigPath ..\canvas.config.1.json -AssignmentId 67890 -Apply # PUT $B/assignments/67890/submissions/111"; e = $true; l = 'allow: Post-Grades.ps1 (sanctioned) hitting /submissions' },
    @{ t = 'powershell -File Post-Grades.ps1 -ConfigPath ..\canvas.config.1.json -AssignmentId 67890 # reads grading\67890\map.json'; e = $true; l = 'allow: Post-Grades.ps1 (sanctioned) reading local grading\ map.json' },
    @{ t = "Invoke-RestMethod $B/enrollments";                   e = $false; l = 'DENY: /enrollments' },
    @{ t = "Invoke-RestMethod $B/users";                         e = $false; l = 'DENY: /users' },
    @{ t = "Invoke-RestMethod $B/students";                      e = $false; l = 'DENY: /students' },
    @{ t = "Invoke-RestMethod $B/assignments/15943978/submissions"; e = $false; l = 'DENY: /assignments/:id/submissions (deny beats allow)' },
    @{ t = "Invoke-RestMethod $B/assignments/67890/submissions?include[]=user"; e = $false; l = 'DENY: GENERIC command (no sanctioned script) hitting /submissions' },
    @{ t = 'Get-Content .\grading\67890\map.json';               e = $false; l = 'DENY: GENERIC command reading grading\ map.json (boundary holds for non-sanctioned)' },
    @{ t = "Invoke-RestMethod $B/gradebook/feed";                e = $false; l = 'DENY: /gradebook' },
    @{ t = "Invoke-RestMethod $B/analytics/users/3/assignments"; e = $false; l = 'DENY: /analytics + /users' },
    @{ t = 'Get-Content .\private\users.raw.json';               e = $false; l = 'DENY: read of private/ cache' },
    @{ t = 'type C:\proj\grading\721874\15943978\submissions.index.json'; e = $false; l = 'DENY: read of grading/ cache' },
    @{ t = "Invoke-RestMethod $B/some_new_endpoint";             e = $false; l = 'DENY: unrecognized Canvas endpoint (fail-closed)' },
    @{ t = "Invoke-RestMethod $B/pages/player-profile-system";   e = $true;  l = 'allow: page slug containing "profile" not over-blocked' }
)
foreach ($c in $policy) {
    $r = Test-CanvasCallAllowed -Text $c.t
    Check ($r.allowed -eq $c.e) ("$($c.l)")
}

$lines += ""
$lines += "== REDACTION (engine) =="
$red = @(
    @{ t = 'Reach me at jane.doe@example.edu or 601-555-0142, SIS 900112233.'; p = 'Standard';
       remove = @('jane.doe@example.edu','601-555-0142','900112233'); l = 'email + phone + 9-digit (Standard)' },
    @{ t = '{"sortable_name":"Doe, Jane","login_id":"jdoe@example.edu","sis_user_id":"900112233"}'; p = 'Standard';
       remove = @('Doe, Jane','jdoe@example.edu','900112233'); l = 'PII JSON fields (Standard)' },
    @{ t = 'https://x.instructure.com/api/v1/courses/1/users/4242?user_id=4242 then /submissions/777'; p = 'Standard';
       remove = @('4242','/777'); l = 'Canvas user-id URL params (Standard)' },
    @{ t = '{"name":"Jane Doe","sortable_name":"Doe, Jane"}'; p = 'Strict';
       remove = @('Jane Doe','Doe, Jane'); l = 'name fields (Strict profile)' },
    @{ t = 'student login R80125209 with sis 951.R70158616 (MGCCC formats)'; p = 'Standard';
       remove = @('R80125209','951.R70158616'); l = 'MGCCC login_id + sis_user_id formats (Standard)' },
    @{ t = 'defensive over-length check: id 211.M105723849 and M10573849'; p = 'Standard';
       remove = @('211.M105723849','M10573849'); l = 'defensive: an over-length ID is still caught (REAL MGCCC format is letter + 8 digits)' },
    @{ t = '{"sortable_name":"Smith, Bob","short_name":"Bob Smith","sis_user_id":"211.M10534634","login_id":"M10534634","email":"someone@example.edu"}'; p = 'Standard';
       remove = @('Smith, Bob','Bob Smith','211.M10534634','M10534634','someone@example.edu'); l = 'real MGCCC roster record JSON (Standard)' }
)
foreach ($c in $red) {
    $o = Invoke-PiiRedaction -Text $c.t -Profile $c.p
    foreach ($tok in $c.remove) { Check (-not $o.Text.Contains($tok)) ("$($c.l): removed '$tok'") }
    Check ($o.Count -gt 0) ("$($c.l): redaction count > 0 (was $($o.Count))")
}

$lines += ""
$lines += "== CONTENT SAFETY (no over-redaction under Standard) =="
$content = '{"name":"Week 5 Quiz","points_possible":25,"published":true}'
$co = Invoke-PiiRedaction -Text $content -Profile 'Standard'
Check ($co.Text.Contains('Week 5 Quiz')) 'content name "Week 5 Quiz" preserved (Standard)'
Check ($co.Count -eq 0) 'no redactions on content JSON (Standard)'

# Honest disclosure: things the block hook does NOT catch (NOT counted in pass/fail).
# These are by-design boundaries, mitigated by a scoped Canvas token + no-PII tools in
# the skill. See CONTINGENCY-ANALYSIS.md.
$lines += ""
$lines += "== KNOWN LIMITATIONS (disclosed; NOT security guarantees) =="
$limits = @(
    @{ t = 'powershell -NoProfile -File C:\tmp\export.ps1'; l = 'script-FILE indirection (a script could fetch PII; its command line shows no URL)' },
    @{ t = 'powershell -EncodedCommand SQBuAHYAbwBrAGUA'; l = 'EncodedCommand / obfuscated commands are not decoded' },
    @{ t = 'use the canvas MCP tool to get submissions for course 1'; l = 'MCP tool calls are not matched by the Bash|PowerShell|WebFetch hook' }
)
foreach ($c in $limits) {
    $r = Test-CanvasCallAllowed -Text $c.t
    $verdict = if ($r.allowed) { 'ALLOWED (gap)' } else { 'blocked' }
    $lines += ("  NOTE  {0}  ->  {1}" -f $c.l, $verdict)
}
$lines += "        Mitigation for all of the above: a SCOPED Canvas token (no view-grades/students),"
$lines += "        which makes student PII unfetchable at the source. See DATA-HANDLING.md."

# Render report
$header = @(
    "canvas-pii-guard - coverage report",
    ("Result: {0} passed, {1} failed" -f $pass, $fail),
    "(synthetic data only; proves listed structured-PII forms, not arbitrary free text)",
    ""
)
$report = ($header + $lines) -join "`r`n"
$reportPath = Join-Path $PSScriptRoot 'guard-coverage-report.txt'
[IO.File]::WriteAllText($reportPath, $report)
Write-Host $report
Write-Host ""
if ($fail -eq 0) { Write-Host "ALL TESTS PASSED. Report: $reportPath" }
else { Write-Host "$fail TEST(S) FAILED. Report: $reportPath" }
exit $fail
