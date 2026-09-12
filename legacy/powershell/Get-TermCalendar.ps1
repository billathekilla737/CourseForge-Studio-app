<#
  Get-TermCalendar.ps1
  Given a start date, infer the MGCCC term name and return that term's instructional
  anchors (finalsEnd + breaks) from the machine-readable JSON block in
  references/academic-calendar.md. This lets Set-DueDates.ps1 -Auto schedule a course
  with NO instructor input: start comes from Canvas, length from the Week-N modules,
  and finals/breaks from this lookup.

  TERM INFERENCE (from the start date's month):
    month >= 8        -> "Fall <year>"
    months 1-4        -> "Spring <year>"
    months 5-7        -> "Summer <year>"

  If the inferred term is NOT in the calendar JSON, this returns $null and the caller
  must fall back to asking the instructor for finals + breaks.

  ENCODING (SKILL Gotcha 6): pure ASCII. No $PSScriptRoot in param defaults (the
  calendar path is resolved inside the body). Dates are parsed/inferred from strings
  only -- no Get-Date / [datetime]::Now reliance, so output is deterministic.

  USE AS A LIBRARY (preferred -- dot-source the function):
    . .\Get-TermCalendar.ps1
    $cal = Get-TermCalendar -StartDate '2026-08-24'
    if ($cal) { $cal.Term; $cal.FinalsEnd; $cal.Breaks }   # else: ask the instructor

  USE STANDALONE (prints the resolved term + anchors):
    .\Get-TermCalendar.ps1 -StartDate 2026-08-24
    .\Get-TermCalendar.ps1 -StartDate 2026-08-24 -CalendarPath ..\references\academic-calendar.md
#>
param(
    [string]$StartDate,
    [string]$CalendarPath
)

$ci = [System.Globalization.CultureInfo]::InvariantCulture

# Capture this script's own directory at top-level scope (works whether the file is
# run directly OR dot-sourced; $MyInvocation.MyCommand.Path is reliable here but null
# inside nested functions). Used to default the calendar path to ../references/.
$script:TermCalScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $script:TermCalScriptDir) { $script:TermCalScriptDir = (Get-Location).Path }

# Infer the term name ("Fall 2026" / "Spring 2026" / "Summer 2026") from a start date.
function Get-TermName {
    param([Parameter(Mandatory)] [string]$StartDate)
    $d = [datetime]::ParseExact($StartDate, 'yyyy-MM-dd', $ci)
    $m = $d.Month
    $y = $d.Year
    if ($m -ge 8)          { return ("Fall {0}"   -f $y) }
    elseif ($m -le 4)      { return ("Spring {0}" -f $y) }
    else                   { return ("Summer {0}" -f $y) }   # months 5-7
}

# Read the term-calendar table out of the academic-calendar.md fenced ```json block.
# Returns a PSCustomObject (parsed JSON) whose properties are term names.
function Read-TermCalendarTable {
    param([string]$CalendarPath)

    if (-not $CalendarPath) {
        # references/academic-calendar.md sits one level up from scripts/.
        $CalendarPath = Join-Path $script:TermCalScriptDir '..\references\academic-calendar.md'
    }
    if (-not (Test-Path $CalendarPath)) {
        throw "academic-calendar.md not found at: $CalendarPath"
    }

    $text = Get-Content -Raw -Encoding UTF8 $CalendarPath
    # Grab the FIRST real fenced json block: a line that is ```json (optional
    # trailing spaces) then a newline, up to the next ``` fence. (?m) anchors ^/$ to
    # lines; (?s) lets . span newlines. Requiring the newline avoids matching an
    # inline ```json mentioned in prose.
    $m = [regex]::Match($text, '(?ms)^```json[ \t]*\r?\n(.*?)^```')
    if (-not $m.Success) {
        throw "No fenced json block found in: $CalendarPath"
    }
    return ($m.Groups[1].Value | ConvertFrom-Json)
}

# Main lookup: start date -> { Term; FinalsEnd; Breaks } or $null if the term is
# not in the table. Breaks comes back as a string[] in -Breaks format.
function Get-TermCalendar {
    param(
        [Parameter(Mandatory)] [string]$StartDate,
        [string]$CalendarPath
    )
    $term  = Get-TermName -StartDate $StartDate
    $table = Read-TermCalendarTable -CalendarPath $CalendarPath

    $entry = $table.PSObject.Properties | Where-Object { $_.Name -eq $term } | Select-Object -First 1
    if (-not $entry) { return $null }
    $val = $entry.Value

    $breaks = @()
    if ($val.breaks) { foreach ($b in @($val.breaks)) { $breaks += [string]$b } }

    return [pscustomobject]@{
        Term      = $term
        FinalsEnd = [string]$val.finalsEnd
        Breaks    = $breaks
    }
}

# When invoked directly (not dot-sourced) with a -StartDate, print the result.
if ($MyInvocation.InvocationName -ne '.' -and $StartDate) {
    $cal = Get-TermCalendar -StartDate $StartDate -CalendarPath $CalendarPath
    if (-not $cal) {
        Write-Host ("Term '{0}' is not in the calendar table -- ask the instructor for finals + breaks." -f (Get-TermName -StartDate $StartDate))
    } else {
        Write-Host ("Term:      {0}" -f $cal.Term)
        Write-Host ("FinalsEnd: {0}" -f $cal.FinalsEnd)
        Write-Host ("Breaks:    {0}" -f $(if ($cal.Breaks.Count) { $cal.Breaks -join ', ' } else { '(none)' }))
        $cal
    }
}
