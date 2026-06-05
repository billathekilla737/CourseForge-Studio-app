<#
  Compute-DueDates.ps1
  Lay a course's instructional weeks onto the calendar and compute a due date per
  week, following the DEFAULT due rule (see references/academic-calendar.md):

    - Each instructional week's items are due the MONDAY AFTER that week, at 23:59
      (the Monday that begins the NEXT calendar week). Both weekday and time are
      overridable (-DueWeekday, -DueTime).
    - SKIP any calendar week that is entirely a break (e.g. Thanksgiving Nov 23-27);
      the instructional weeks step over it.
    - If a computed due day is itself a holiday (Labor Day, a Fall Break day), shift
      it forward to the next non-holiday weekday.
    - The LAST module (the final) is due on the finals-window end date at 23:59.

  NOTE: the exact weekly cadence this produces is a sane default, NOT gospel -- the
  instructor approves the table before it is applied with Set-DueDates.ps1.

  ENCODING (SKILL Gotcha 6): this file is pure ASCII. Dates are parsed with
  [datetime]::ParseExact (no Get-Date / [datetime]::Now reliance) so output is
  fully deterministic.

  Usage:
    .\Compute-DueDates.ps1 -StartDate 2026-08-24 -Weeks 15 -FinalsEnd 2026-12-11 `
        -Breaks '2026-09-07','2026-10-12..2026-10-13','2026-11-23..2026-11-27'
    .\Compute-DueDates.ps1 ... -AsJson > duedates.json
    .\Compute-DueDates.ps1 ... -DueWeekday Friday -DueTime 17:00
#>
param(
    [Parameter(Mandatory)] [string]$StartDate,
    [Parameter(Mandatory)] [int]$Weeks,
    [Parameter(Mandatory)] [string]$FinalsEnd,
    [string[]]$Breaks = @(),
    [string]$DueTime = '23:59',
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
    [string]$DueWeekday = 'Monday',
    [switch]$AsJson
)

$ci = [System.Globalization.CultureInfo]::InvariantCulture

function ParseDay {
    param([string]$s)
    return [datetime]::ParseExact($s, 'yyyy-MM-dd', $ci)
}

# Expand the -Breaks list ('yyyy-MM-dd' single days or 'yyyy-MM-dd..yyyy-MM-dd'
# ranges) into a set of holiday dates (date-only) for fast membership tests.
$holidays = @{}
foreach ($b in $Breaks) {
    if (-not $b) { continue }
    if ($b -match '\.\.') {
        $parts = $b -split '\.\.'
        $d0 = (ParseDay $parts[0].Trim()).Date
        $d1 = (ParseDay $parts[1].Trim()).Date
        $d  = $d0
        while ($d -le $d1) { $holidays[$d.ToString('yyyy-MM-dd')] = $true; $d = $d.AddDays(1) }
    } else {
        $holidays[(ParseDay $b.Trim()).Date.ToString('yyyy-MM-dd')] = $true
    }
}

function Is-Holiday {
    param([datetime]$d)
    return $holidays.ContainsKey($d.Date.ToString('yyyy-MM-dd'))
}

# The Monday (or chosen weekday) at or before a given date -- start of its calendar
# week. .NET DayOfWeek: Sunday=0..Saturday=6. We treat Monday as the week start.
function WeekStartMonday {
    param([datetime]$d)
    $dow = [int]$d.DayOfWeek          # Sun=0 .. Sat=6
    $delta = if ($dow -eq 0) { 6 } else { $dow - 1 }   # days since Monday
    return $d.Date.AddDays(-$delta)
}

# Is the calendar week beginning on $weekStartMonday a full no-class week? We test
# the WEEKDAYS (Mon..Fri) only -- weekends are never class days, so a break that
# covers Mon-Fri (e.g. Thanksgiving Nov 23-27) counts as a full break week even
# though Sat/Sun are not listed as holidays. If so the schedule skips it.
function Is-FullBreakWeek {
    param([datetime]$weekStartMonday)
    for ($i = 0; $i -lt 5; $i++) {
        if (-not (Is-Holiday $weekStartMonday.AddDays($i))) { return $false }
    }
    return $true
}

# Offset from a week-start Monday to the chosen due weekday within that week.
$weekdayOffset = @{ Monday=0; Tuesday=1; Wednesday=2; Thursday=3; Friday=4; Saturday=5; Sunday=6 }

# If a target due day is a holiday, push it forward to the next non-holiday weekday
# (skip Sat/Sun too so a shifted due date lands on a class day).
function Shift-PastHolidays {
    param([datetime]$d)
    $x = $d.Date
    while ((Is-Holiday $x) -or ($x.DayOfWeek -eq [DayOfWeek]::Saturday) -or ($x.DayOfWeek -eq [DayOfWeek]::Sunday)) {
        $x = $x.AddDays(1)
    }
    return $x
}

$start     = ParseDay $StartDate
$finalsEndD = ParseDay $FinalsEnd

# Time-of-day to append. Validate HH:mm.
if ($DueTime -notmatch '^\d{2}:\d{2}$') { throw "DueTime must be HH:mm (got '$DueTime')" }
$dueHour   = [int]($DueTime.Substring(0,2))
$dueMin    = [int]($DueTime.Substring(3,2))

# Walk calendar weeks. Instructional week N occupies the first non-fully-break
# calendar week not yet consumed; its due day is the chosen weekday of the FOLLOWING
# calendar week, shifted past any holiday. The last week (the final) is forced to
# the finals-window end date.
$results = @()
$weekStart = WeekStartMonday $start    # Monday of the term-start week

for ($n = 1; $n -le $Weeks; $n++) {

    # Advance past any calendar week that is entirely a break BEFORE this
    # instructional week occupies it.
    while (Is-FullBreakWeek $weekStart) { $weekStart = $weekStart.AddDays(7) }

    if ($n -eq $Weeks) {
        # Final module: due on the finals-window end date at the due time.
        $dueDay = $finalsEndD.Date
    } else {
        # Due the chosen weekday of the NEXT calendar week.
        $nextWeekStart = $weekStart.AddDays(7)
        # If that next week is a full break (e.g. Thanksgiving), step over it so the
        # due date lands on a real week.
        while (Is-FullBreakWeek $nextWeekStart) { $nextWeekStart = $nextWeekStart.AddDays(7) }
        $dueDay = $nextWeekStart.AddDays($weekdayOffset[$DueWeekday])
        $dueDay = Shift-PastHolidays $dueDay
    }

    $dueAt = [datetime]::new($dueDay.Year, $dueDay.Month, $dueDay.Day, $dueHour, $dueMin, 0)
    $results += [pscustomobject]@{
        Week   = $n
        DueDate = $dueDay.ToString('yyyy-MM-dd')
        DueAt   = $dueAt.ToString('yyyy-MM-ddTHH:mm:00')
    }

    # Consume this calendar week; move to the next for the next instructional week.
    $weekStart = $weekStart.AddDays(7)
}

if ($AsJson) {
    $results | ConvertTo-Json -Depth 4
} else {
    Write-Host ("Term start {0}  |  {1} weeks  |  finals end {2}  |  due {3} {4}" -f `
        $start.ToString('yyyy-MM-dd'), $Weeks, $finalsEndD.ToString('yyyy-MM-dd'), $DueWeekday, $DueTime)
    Write-Host ("Breaks: {0}" -f ($(if ($Breaks.Count) { $Breaks -join ', ' } else { '(none)' })))
    Write-Host ""
    $results | Format-Table -AutoSize Week, DueDate, DueAt | Out-Host
    Write-Host "NOTE: cadence is a default for the instructor to approve before applying."
    # Emit objects to the pipeline too, for programmatic callers.
    $results
}
