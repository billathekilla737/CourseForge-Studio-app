# Who needs a look

A trial. If the scan is too slow, delete this feature by following the list
below. It does not email anyone, and it does not put a notice on the home page.

## What it does

On launch, Studio starts the check once in the background. A chip in the
header shows “Reading 3 of 5” and a bar while it is reading, then “Finished”
or “N need a look” when the job’s state is done. The same bar is on the
Students page, with the last scanned date. The count is classes finished,
not the class it is about to read.

On **Students**, **Check this term** reads the assignment list for each class
you are teaching now. Submissions are fetched only for items that are due and
were not in the last scan. A later check leaves those alone and adds whatever
has come due since `scanned_at`.

Each student gets two scores from 0 to 100 in each class. Higher means they
need a look sooner. **Work** is grades and missing assignments. **Attend** is
absences and tardies, and it is a separate number. The list shows the highest
class score of each. The student page shows every class. A second check
compares with the previous one: `+8` means that score rose by 8.

The points are fixed, not a hidden model.

Work:

| What Studio sees | Points, capped |
|---|---|
| Grade under 60% | 35 |
| Grade under 70% | 20 |
| Grade under 80% | 8 |
| Each past-due item not turned in | 10, up to 40 |
| Each zero on a turned-in item | 5, up to 20 |
| Missing work and no submission for 14 days | 12 |
| No Canvas activity for 14 days | 8 |

Attend, from the attendance calendar already on this computer. Excused and
present add nothing. Tardies are counted, lightly:

| What Studio sees | Points, capped |
|---|---|
| Each absence | 12, up to 72 |
| Each tardy | 2, up to 16 |

0–24 is low, 25–49 is medium, 50 and above is high, for each score on its
own. "Needs a look" is a student who is high on either one. Nothing is sent
to Claude.

## Where the result is saved

`data/risk-index.json` on this computer, and a copy in your Canvas user files
at `courseforge-studio/state/risk-index.json`. Both hold the last scanned
date, which assignments were already included, the scores, and the last few
scans. The Canvas copy is loaded before a scan if it is newer, so another
computer does not read those assignments again. Deleting both forgets the
history and the next check reads every due item once more.

The check writes how many seconds it took into that file (`seconds`) and onto
the Students page.

## How to remove it

1. Delete `courseforge/studentsarea/risk.py`.
2. Delete this file, `docs/RISK-INDEX.md`.
3. Delete `tests/test_risk.py`.
4. In `courseforge/studentsarea/routes.py`, remove the import of `risk` and
   the two routes marked `RISK-INDEX` (`GET` and `POST /api/students/risk`).
5. In `courseforge/studentsarea/__init__.py`, remove the RISK-INDEX paragraph.
6. In `courseforge/web/js/student.js`, remove the blocks marked `RISK-INDEX`:
   `riskById`, `riskMark`, `repaintStudents`, the "Who needs a look" section
   and its progress bar, the header chip, the `studio:booted` listener that
   starts the check, the "Needs a look" sort option, `paintRiskSummary`,
   `paintRiskProgress`, `applyRisk`, `loadSavedRisk`, `runRiskScan`,
   `watchRiskJob`, `paintStudentRisk`, and the calls to them.
7. In `courseforge/web/css/student.css`, remove `.stRiskBox`, `.stRisk`,
   `.stScore`, `.stKey`, `.stHint`, `.stBar`, and `.riskChip`.
8. Delete `data/risk-index.json` if it is there, and
   `Files → courseforge-studio → state → risk-index.json` in Canvas.
9. The `student.js` and `student.css` lines in `courseforge/web/index.html`
   can stay; they load the rest of the Students page.

No other package imports `studentsarea.risk`.
