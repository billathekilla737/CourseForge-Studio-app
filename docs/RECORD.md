# The record of actions

## Why it exists

A student says they were never given the extra time their accommodation
letter grants. Two years later the college has to show otherwise. Canvas
itself can answer some of this, if somebody knows which admin report to pull
and the retention window has not closed. What nobody has is a plain account,
made at the time, of what was set, for whom, by whom, and whether Canvas
accepted it.

The Studio has that account, because the Studio is the thing that did it. So
it writes one down.

This is not a legal document and it does not replace Canvas's own logs. It is
a contemporaneous record made by the tool that performed the action, of the
kind that ends an argument early rather than starting one.

## What is recorded

Every Canvas write the Studio makes, and every decision that produced a grade
before one reached Canvas. The second half matters as much as the first: a push
entry says a number was written, and cannot say who or what decided it. In
practice:

| Area | What it says |
|---|---|
| Accommodations | Which students, which quiz, how much extra time, and whether Canvas took it. Refusals are recorded too. |
| Grade | How many scores went to which assignment, for which students, with the score, and whether they landed hidden or visible. Showing and hiding grades are their own entries. |
| Grade, before Canvas | The grading run itself: which model read whose work, against which rubric, what it proposed, what it cost, and how many it flagged for a person. Then every curve, every score or comment changed by hand over what the model proposed, and every export of the grades to a spreadsheet. None of these touch Canvas, and all of them are the answer to "who decided this". |
| Accessibility | Pages, documents and PDFs restyled, fixed or uploaded, with counts. |
| Build | Content placed in the course. |
| Tools | Due dates moved, exports, imports, navigation changes, quiz backups. Term-schedule changes are recorded against the course they touched, not as one line about "the schedule". |
| Extensions | A deadline moved for one student and not the rest of the class: who, which assignment, the date before and the date after, how many days, the absence it was for, and which date it was measured from. Canvas refusing one is recorded the same way. |
| Tests | Quiz settings: what changed, on which quiz. |
| Messages | Private Canvas messages to students who have turned nothing in: who was written to, the subject, and who could not be reached. |
| Assistant | Each Allow you clicked, and what it allowed. |

Accommodations, grades and messages name the student, by Canvas user id and by name,
because a record of accommodations that does not say who they were for answers
nothing. Everything else names the page or the file.

Nothing in this file is ever sent to a model. The Assistant's tags live in
`courseforge/identity.py` and the grader's in `courseforge/pseudonym.py`;
neither reads this.

## Where it is kept

Two places, and both matter.

**On this computer**, at `data/<course>/audit/<YYYY-MM>.jsonl`, one file a
month, one JSON object a line. The account-wide audit chain (every course
together) is at `data/audit/`. That folder is **not** the standing
accommodation roster. The roster is `data/accommodations.json`, and a copy
is kept in your Canvas user files under `courseforge-studio/state/` so a
second computer can pick it up.

**In Canvas**, in your own user files under **Files → courseforge-studio →
record**. Not the course's files: a course copy, a term rollover and a sandbox
cleanup all leave your user files alone, and nobody enrolled in the course can
see them. Canvas stamps its copy with a modified date that nothing here
controls, which is the point.

The upload happens in the background every few minutes, and only for months
that have changed. `audit_to_canvas: false` in `config.json` turns it off, and
so does one switch on the Record screen; the local chain is written either
way.

## Why it can be trusted a little further than a text file

Each entry carries `prev`, the SHA-256 fingerprint of the entry before it, and
`hash`, its own. The file is a chain. Change a sentence, delete a row, or slip
one in after the fact, and every fingerprint from that point stops matching.

    python -m courseforge record --course 734975 --verify

says whether it adds up, and if not, at which entry and why. It tells three
failures apart, because they mean different things:

- the entry does not match its own fingerprint — its text was edited;
- its `prev` does not match the entry before it — a row was removed or added;
- the numbering skips — the file is not the one that was written.

**This is tamper-evident, not tamper-proof.** Anyone who can write the file
can rebuild the chain from the point they changed. What they cannot do is make
a quiet single-line edit, and they cannot reach back into the copy Canvas
already holds without that showing as a new upload on a later date. The value
is the same as a bound notebook against a stack of loose paper.

## Reading it

**In the Studio**: the Record tab inside a course. It leads with whether the
chain still adds up, says where the file is on disk and in Canvas, and filters
by student, by part of the Studio, and by month. Picking a student gives you
everything that was done for that student, which is the two clicks the dispute
actually needs.

**From a terminal**, including on a machine with the data folder and no
Studio:

```bash
python -m courseforge record --course 734975 --verify
python -m courseforge record --course 734975 --student 900111
python -m courseforge record --course 734975 --sync     # push to Canvas now
python -m courseforge record --course account           # the account-wide audit chain, not the roster
```

**From Canvas**: download the month file from your user files. It is
newline-delimited JSON; every line stands alone and reads without this tool.

## What it does not do

- It does not record reads. Opening a course, listing files and pulling
  grades leave no entry; only changes do. A slider moved back to where it
  started is not a change and writes nothing.
- It does not record every keystroke of a hand edit. One entry is written
  when a score or a comment ends up different from what was there before,
  not one per drag.
- It does not record what someone did directly in Canvas. If the extra time
  was set in the Canvas UI rather than here, this file will not know.
- It does not sign anything. There is no key, so the chain proves internal
  consistency and nothing about who wrote it beyond the Canvas account name
  it recorded at the time.
- It is not a retention policy. Nothing deletes these files; that is a
  decision for whoever owns the records, not for this tool.
