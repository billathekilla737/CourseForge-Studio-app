# Academic calendar (term scheduling + due dates)

This reference fixes the term anchor dates (start, finals, breaks) used to compute
due dates, and states the default due rule that `scripts/Compute-DueDates.ps1`
applies. It pairs with `scripts/Set-DueDates.ps1`, which writes the computed dates
onto a course's assignments and quizzes.

## Source of truth (re-check yearly)
The authoritative dates are the MGCCC academic calendar:
**https://mgccc.edu/about/academic-calendar/**

These dates SHIFT every academic year (a term start can move several days, breaks
land on different weekdays, finals weeks move). Treat the values below as a
**snapshot for Fall 2026 only**. Before scheduling any term, open the calendar page,
confirm the start / finals / break dates for that specific term and format, and
update this file. Do not assume last year's offsets carry forward.

## MGCCC term formats
A course is offered in one of several **formats**; each has its own start date,
number of instructional weeks, and finals window:

- **Full-term (15-week)** — the standard semester.
- **1st 8-week (1st term)** — the first half of the semester.
- **2nd 8-week (2nd term)** — the second half of the semester.
- **13-week** — a late-start full-ish term.
- **4-week** — short intensive (e.g. intersession / mini-term).

Face-to-face and online sections of the "same" term can start on **different days**
(online sections often start a few days after the face-to-face start). Always pull
the row that matches BOTH the term and the delivery mode.

## Fall 2026 anchor dates (snapshot)
Start dates:
- **Full-term & 1st-term FACE-TO-FACE start:** Thursday, August 20, 2026.
- **Online 15-week start:** Monday, August 24, 2026.
- **13-week start:** Tuesday, September 8, 2026.
- **2nd 8-week start:** Wednesday, October 14, 2026.

Breaks (no class):
- **Labor Day:** Monday, September 7, 2026.
- **Fall Break:** Monday-Tuesday, October 12-13, 2026.
- **Thanksgiving:** Monday-Friday, November 23-27, 2026 (a full no-class week).

Finals:
- **Full-term finals:** December 7-11, 2026 (grades due Friday, December 11 at noon).
- **1st-term (1st 8-week) finals:** October 8-9, 2026.

When calling `Compute-DueDates.ps1`, pass these as:
- `-StartDate` = the start row that matches the term + mode (e.g. `2026-08-24` for the
  online 15-week).
- `-FinalsEnd` = the last day of the finals window (e.g. `2026-12-11` full-term).
- `-Breaks` = the break ranges, e.g.
  `'2026-09-07','2026-10-12..2026-10-13','2026-11-23..2026-11-27'`.

## DEFAULT due rule
`Compute-DueDates.ps1` lays the instructional weeks onto consecutive calendar weeks
starting from the term start and assigns due dates by this rule:

1. **Each instructional week's items are due the MONDAY AFTER that week, at 23:59**
   (11:59 PM) — that is, the Monday that begins the next calendar week. (Both the
   weekday and the time are overridable: `-DueWeekday`, `-DueTime`.)
2. **Skip any calendar week that is entirely a break.** Thanksgiving week
   (Nov 23-27) is a full no-class week, so the instructional weeks step over it and
   resume the following week.
3. **If a computed due-Monday is itself a holiday** (Labor Day, a Fall Break day),
   shift it forward to the next non-holiday weekday.
4. **The LAST module (the final) is due on the finals-window end date at 23:59**, not
   on a Monday — e.g. the full-term final lands on December 11.

The exact weekly cadence this produces is a sane default, **not gospel** — the
instructor approves the table before it is applied. Show the week-by-week table,
get sign-off, then run `Set-DueDates.ps1` (dry-run first).
