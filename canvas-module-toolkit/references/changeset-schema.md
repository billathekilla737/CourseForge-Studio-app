# Changeset and quiz JSON schemas

These are the two file formats the toolkit's scripts read and write. Both are plain
JSON so any agent or human can hand-author them with a text editor - nothing here is
Claude-specific or Codex-specific.

## 1. The changeset file (`Push-CanvasModule.ps1 -Changeset ...`)

```jsonc
{
  "course_id": "734709",                    // optional if canvas.config.<id>.json resolves it
  "base_url": "https://school.instructure.com",  // optional, same reason

  "page": {
    "slug": "m7-slash-notes-security-and-personnel",   // the Canvas page url slug
    "body_file": "new/page_notes.html"                 // path, relative to the changeset file
  },

  "assignment": {
    "id": "16099307",                        // the Canvas assignment id
    "description_file": "new/assignment.html"
  },

  "quiz": {
    "id": "5381766",                         // the Canvas quiz id

    // EITHER supply the description separately...
    "description_file": "new/quiz_description.html",
    // ...OR embed it inside questions_file's {description, questions} wrapper (below) -
    // description_file/description win if both are present.

    "questions_file": "new/quiz.json",       // rebuilds ALL questions - see schema below
    "expected_question_count": 14,           // REQUIRED in practice: refuses to run if
                                              // questions_file's count differs, since Canvas
                                              // quiz points are 1-per-question by default and
                                              // a silent count change silently changes the
                                              // quiz's total points
    "require_unpublished_course": true       // default true; refuses to rebuild questions
                                              // unless the COURSE itself is unpublished,
                                              // which is the only state where student
                                              // attempts cannot exist yet. Only set false
                                              // after you have verified independently
                                              // (e.g. checked the submissions count through
                                              // a sanctioned/read-only path) that no
                                              // attempts exist.
  }
}
```

Any of `page` / `assignment` / `quiz` may be omitted entirely - only what's present is
touched. Nothing else on the page/assignment/quiz (title, points, due date, publish
state, module placement) is ever modified by this script.

## 2. The quiz questions file (`questions_file`, and `Get-CanvasModule.ps1`'s dump format)

Two accepted shapes - use whichever is more convenient to author:

**Bare array** (what `Get-CanvasModule.ps1` dumps):
```json
[
  {
    "name": "Q1 Short internal label",
    "text": "<p>The question text students see, as HTML.</p>",
    "incorrect": "Feedback shown when a student picks a wrong answer.",
    "answers": [
      ["The correct option",   100],
      ["A wrong option",         0],
      ["Another wrong option",   0],
      ["A third wrong option",   0]
    ]
  }
]
```

**Wrapped, with an embedded description** (handy when you're hand-authoring, since the
quiz's intro text lives right next to the questions it introduces):
```json
{
  "description": "<div>...intro HTML shown before the quiz starts...</div>",
  "questions": [ /* same array shape as above */ ]
}
```

Each `answers` entry is `[text, weight]`. Weight `100` means "this is correct, full
credit"; every other option should be `0`. **Exactly one answer per question must have
a nonzero weight** - `scripts/check_quiz.py` enforces this before anything reaches
Canvas, because a question with zero or two+ correct answers is either unanswerable or
silently gives credit for a wrong pick, and both are much cheaper to catch locally than
to discover after a student has taken the quiz.

Run the checker before every push:
```bash
python3 scripts/check_quiz.py new/quiz.json --expect-count 14
```

## 3. `Get-CanvasModule.ps1`'s `ids.json` output

A small convenience file written after exploring a module, meant to be copied straight
into a new changeset's `page.slug` / `assignment.id` / `quiz.id` fields instead of
hunting through `module.json`:

```json
{
  "course_id": "734709",
  "base_url": "https://mgccc.instructure.com",
  "module_id": "5106504",
  "page_slug": "m7-slash-notes-security-and-personnel",
  "assignment_id": "16099307",
  "quiz_id": "5381766"
}
```

If a module has more than one assignment with an HTML body, later ones are recorded
under `assignment_id_<id>` so nothing is silently dropped - check `module.json` for the
full inventory in that case.
