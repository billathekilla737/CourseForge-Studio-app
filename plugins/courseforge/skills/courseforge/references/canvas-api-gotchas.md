# Canvas API + PowerShell 5.1 gotchas (silent-failure catalog)

Every entry here was found the hard way: the API returned **HTTP 200** and changed nothing,
or returned data that did not match what was actually stored. None of them raise an error,
so the only defense is to **read the value back and compare** after every write.

Read this before writing any new script that PUTs to Canvas.

---

## 1. `ConvertTo-Json` silently corrupts long strings (PS 5.1)

`ConvertTo-Json` can serialize a long string as an **object** instead of a string:

```powershell
@{ assignment = @{ description = $html } } | ConvertTo-Json -Depth 5
# -> {"assignment":{"description":{"value":"<div ...","Count":13956}}}
```

Canvas cannot coerce that object to a string, so it **drops the parameter, returns 200, and
leaves the content unchanged**. A 14 KB body ballooned to 400 KB and the push "succeeded"
while the page was untouched.

**Never use `ConvertTo-Json` for a request body.** Hand-build it with an escaper:

```powershell
function J-Str([string]$s) {
    $sb = New-Object System.Text.StringBuilder; [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        switch ($ch) {
            '"' { [void]$sb.Append('\"') } '\' { [void]$sb.Append('\\') }
            "`n" { [void]$sb.Append('\n') } "`r" { [void]$sb.Append('\r') } "`t" { [void]$sb.Append('\t') }
            default { $i = [int]$ch
                      if ($i -lt 32 -or $i -gt 126) { [void]$sb.Append(('\u{0:x4}' -f $i)) }
                      else { [void]$sb.Append($ch) } }
        }
    }
    [void]$sb.Append('"'); return $sb.ToString()
}
$body = '{"assignment":{"description":' + (J-Str $html) + '}}'
Invoke-RestMethod -Method Put -Uri $u -Headers $hdr `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

**Detection:** the returned `description.Length` equals the OLD length. Always compare.

---

## 2. `@(Invoke-RestMethod ...)` inline nests the array

This is the single most repeated mistake when working with Canvas list endpoints:

```powershell
$items = @(Invoke-RestMethod -Uri "$base/courses/$id/quizzes" -Headers $hdr)
$items.Count            # 1  -- WRONG, regardless of how many quizzes exist
$items | Measure-Object position -Maximum   # throws: property not found
```

The JSON array becomes a single nested element. **Assign first, then wrap:**

```powershell
$raw   = Invoke-RestMethod -Uri "$base/courses/$id/quizzes" -Headers $hdr
$items = @($raw)
$items.Count            # correct
```

`foreach` unwraps either way, which is why the bug hides: loops look fine while `.Count`,
`Measure-Object`, `Where-Object` and `[0]` all misbehave.

---

## 3. An unpublished Classic Quiz serves a STALE snapshot on GET

For `published: false` quizzes, `GET /courses/:id/quizzes/:qid` returns the values from the
last **published** state, not what is stored now. Observed on a quiz whose questions had
been fully replaced:

| field | GET returned | actually stored |
|---|---|---|
| `points_possible` | 100 | 150 |
| `question_count` | 31 | 25 |
| `time_limit` | 45 | 90 |
| `description` | old text | new text |

The writes **did** land — the PUT response echoed the new values — but every subsequent GET
(single *and* list endpoint) reported the old ones. Publishing materializes the real state.

**Consequences:**
- Do not "verify" an unpublished quiz by re-reading it. Trust the PUT echo, or verify via
  `/quizzes/:qid/questions`, which is always accurate.
- Do not conclude a write failed just because the read disagrees. Hours were lost trying
  encodings, formats and publish toggles against a write that had already succeeded.

---

## 4. `points_possible` does not recompute after API question edits

A **published** Classic Quiz caches `points_possible` and `question_count` on the quiz row
and only regenerates them on a publish transition. Add or delete questions over the API and
the quiz — **and the gradebook assignment** — keep reporting the old total. A plain PUT does
not clear it.

Force it by toggling published across a boundary and restoring the original state:

```powershell
# for a quiz that is currently published
PUT /quizzes/:id  {"quiz":{"published":false}}
PUT /quizzes/:id  {"quiz":{"published":true}}
```

Never publish a quiz the instructor left unpublished just to refresh a number; for those,
see gotcha 3 — the totals correct themselves when the instructor publishes.

---

## 5. `POST /assignments/:id/duplicate` returns 400 for quiz-backed assignments

Classic Quizzes cannot be duplicated through the assignment duplicate endpoint. To snapshot
one, create a new quiz and re-POST every question. `scripts/Backup-CanvasQuiz.ps1` does this.

---

## 6. Quiz question READ shape is not the WRITE shape

Reading a question returns answers as `text` / `left` / `right` / `weight`. Creating one
expects `answer_text` / `answer_match_left` / `answer_match_right` / `answer_weight`. Copy a
question without mapping and you get answers with blank text and no correct option — and no
error.

| read | write |
|---|---|
| `answers[].text` | `answer_text` |
| `answers[].weight` | `answer_weight` |
| `answers[].left` (matching) | `answer_match_left` |
| `answers[].right` (matching) | `answer_match_right` |
| `answers[].comments` | `answer_comments` |

`matching_answer_incorrect_matches` carries over unchanged.

---

## 7. Course `start_at` / `end_at` will not persist unless participation is "Course"

`PUT /courses/:id` with `course[start_at]` while
`restrict_enrollments_to_course_dates` is **false** does not just fail — it can **null both
dates**. Setting one date cleared an existing `start_at` AND `end_at`.

They only stick when sent together with the flag:

```powershell
course[restrict_enrollments_to_course_dates]=true&course[start_at]=...&course[conclude_at]=...
```

Note `conclude_at` on write, `end_at` on read. **Flipping that flag changes student
participation behavior** (course dates start governing access instead of term dates), so
surface it to the instructor rather than setting it silently.

---

## 8. canvas-pii-guard fails closed on URLs built from variables

The guard inspects the literal command text. A URL assembled from a variable has no visible
course id, so the fail-closed rule in step 4 of `Test-CanvasCallAllowed` denies it:

```powershell
# DENIED - the guard sees "/courses/$cid/pages", which does not match /courses/\d+
Invoke-RestMethod -Uri "$base/courses/$cid/pages" -Headers $hdr
```

Two compliant options: write the id literally in ad-hoc commands, or put the loop in a
`.ps1` and run the script (the documented script-file indirection). This is correct guard
behavior, not a bug — but it surprises every agent once.

---

## 9. Deleting a module deletes references, not content

`DELETE /courses/:id/modules/:mid` removes the module and its items. The underlying
assignments, pages and quizzes survive. That makes it the safe way to retire a staging or
duplicate module.

**But** an item that existed *only* in that module becomes orphaned: still in the gradebook,
unreachable from any module. Before deleting, verify every content item appears somewhere
else:

```powershell
# build a map of content_id -> module for all OTHER modules, then assert
# every item in the doomed module has an entry
```

---

## 10. Assignment shells reject description writes

Quiz- and discussion-backed assignments are shells. `PUT assignment[description]` returns
**400**; edit the quiz `description` or the discussion `message` instead. Filter them out:

```powershell
if ($a.quiz_id -or $a.discussion_topic) { continue }
```

---

## The rule that catches all of these

**Write, then read back the specific field and compare.** For unpublished quizzes, verify
through `/questions` rather than the quiz object. Report a mismatch loudly instead of
printing "done" — several of the failures above are indistinguishable from success unless
you check.
