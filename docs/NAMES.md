# Student names and what leaves the machine

Two parts of the Studio talk to a model about students, and both swap the
names first. They do it differently because they are doing different jobs, and
knowing which is which matters if anyone asks you to defend it.

| | Grading | The Assistant |
|---|---|---|
| Module | `courseforge/pseudonym.py` | `courseforge/identity.py` |
| Tag | `S-001`, `S-002` | `Student-1`, `Student-2` |
| Scope | One assignment | One course |
| Stable | For that assignment | For the life of the course |
| Map kept at | `data/<course>/<assignment>/map.json` | `data/<course>/names.json` |
| Also scrubs | Emails, phone numbers, SIS and user ids, the student's own name inside their writing | — |

Different tag shapes on purpose. A conversation that says `S-014` and a
gradebook that says `S-014` would look like they meant the same person, and
they do not: the grader numbers within one assignment's submissions, the
Assistant within the whole roster.

Neither map file ever leaves the computer. Not to Canvas, not to Anthropic,
not into the grading handoff (`handoff.py` documents `map.json` as the one
file it deliberately does not carry).

## What the Assistant does

Type a real name. Before the message goes anywhere it is swapped for that
student's tag, and the reply's tags are swapped back into names on the way to
the screen. The transcript on disk and the event log keep the tag, because
those are the record of what actually left the machine.

It recognises the full name, the name written last-name-first, the short name,
the login id, the SIS id and the email. It recognises a first or last name on
its own when exactly one student on the roster has it.

## Typing a name you cannot get wrong

Type `@` in the composer and the roster appears. Arrow keys move, Enter or Tab
picks, Escape closes. What goes into the box is the name exactly as Canvas
spells it, so the ordinary exact match does the rest. There is no second
protocol behind it: the picker is a spelling aid, not a separate code path,
which is why it cannot drift out of step with the swap.

The list is filtered as you type, preferring names that start with what you
typed over names that merely contain it.

## A name spelled almost right

This is the failure nobody notices. "Jordn Alvarez" matches no roster entry,
so it is not swapped, so it goes to Anthropic as typed while the person who
wrote it believes it was handled.

So a word that is one or two keystrokes from somebody on the roster stops the
message, names who it thinks you meant, and offers to fix it. Two neighbouring
letters swapped counts as one keystroke, not two, because that is the single
commonest typo there is.

It only considers words of five letters or more, skips a list of capitalised
words that turn up in course prose (days, months, Canvas, Blender, Midterm and
so on), and never fires on a name that matched exactly. It can still be wrong
about an unusual word, so **Send as typed** goes past it in one click. An
ambiguous surname gets no such button: there is no safe way to resolve that
one here.

Three deliberate refusals:

**A name two students share is not guessed.** "Has Okafor turned anything in?"
with two Okafors on the roster stops the message and asks which one. Sending
it would put a real surname in the prompt; picking one would answer about the
wrong student.

**A name that is also an ordinary word needs its full form.** A student called
Casey Long is `Student-5` when you write "Casey Long", and "how long is the
essay" is left exactly as typed. The list is in `ALSO_WORDS`.

**A name that is not on the roster is not a name.** Somebody from another
section goes out as written. The rail on the Assistant screen says
how many students are covered, and **Re-read the roster** picks up anyone who
enrolled since.

## The limit, stated where it is relied on

The swap covers what you type and what Claude writes back. It does not cover
what Claude *reads*. If you Allow a tool call that opens the gradebook or a
file with names in it, those names go to Anthropic exactly as they are
written, because that text never passes through this code on its way out.

The Allow card says so, on the card, for the kinds of call where it is true.
That is the honest place for it: the decision is being made right there.

## Turning it off

`pseudonymize: false` in `config.json` sends names as typed, everywhere. There
is a reason it exists — a model asked to draft a letter to a named student
cannot do it with a tag — but it is off the safe path, and the banner on the
front page changes to say so.
