"""Teaching signal: what a set of results says about the teaching, not the students.

Everything here is deterministic and local. It answers three questions from data
the tool already has:

* **Which rubric rows are not doing their job?** A row everybody aces is not
  measuring anything; a row everybody fails is either untaught or unclear; a row
  that does not track the rest of the grade may be measuring something else
  entirely. This is ordinary item analysis, and it points at the assignment
  rather than at the class.
* **What did students say about it in their own words?** Submissions and Canvas
  comments carry asides that never make it into a score: "I got stuck", "the
  instructions did not say", "my version of the software looks different". Those
  are the most direct feedback an instructor gets and they are usually skimmed
  past on the way to a grade.
* **Does the same weakness keep coming back?** One bad row is an assignment. The
  same row failing across assignments is a curriculum problem.

The judgement about what to *do* is left to the instructor, with an optional
model pass (see grader.teaching_read) that turns this evidence into a plan.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter

from .curve import is_unscored_marker

# ------------------------------------------------------------- student voice
# Each cue is (category, pattern, needs_first_person). Requiring a first-person
# subject for most of them keeps a student's *analysis* out of the results: an
# essay about a game can say "the player gets confused" without the student
# being confused.
# Order matters: the first cue to match wins, so the categories that name a
# concrete cause come before the generic "I was confused" ones. "I got stuck
# because my version of Blender is different" is both, and the useful label is
# the one an instructor can act on.
CUES: list[tuple[str, str, bool]] = [
    ("technical", r"\b(?:version|update|install\w*|crashed?|crashing|freez\w+|"
                  r"would ?n[o']t (?:open|load|run|export|save)|corrupt\w*)\b"
                  r"[^.?!]{0,80}\b(?:different|older|newer|mine|my|problem|issue|error)\b", False),
    ("technical", r"\b(?:i|my)\b[^.?!]{0,60}\b(?:version|laptop|computer|software|blender|"
                  r"unity|program)\b[^.?!]{0,60}\b(?:different|older|newer|crash\w*|"
                  r"would ?n[o']t|could ?n[o']t|does ?n[o']t)\b", False),
    ("instructions", r"\b(?:instructions?|directions?|prompt|assignment|rubric|handout|guidelines?)\b"
                     r"[^.?!]{0,60}\b(?:unclear|confusing|vague|did ?n[o']t say|didnt say|does ?n[o']t say|"
                     r"never said|contradict\w*|ambiguous|hard to (?:follow|read|understand))\b", False),
    ("instructions", r"\b(?:i|we)\s+(?:read|reread|re-read)\s+the\s+"
                     r"(?:instructions?|prompt|rubric|directions?)\b", False),
    ("instructions", r"(?:\bnot|n[o']t)\s+sure\s+(?:if|whether)\s+(?:this|that|i)\s+(?:is|was|did)\s+"
                     r"what\s+(?:you|the assignment|the prompt)\b", False),
    ("missed_resource", r"\b(?:i|we)\s+(?:could ?n[o']t|did ?n[o']t)\s+find\s+the\s+"
                        r"(?:video|reading|tutorial|slides?|link|file|example|rubric)\b", False),
    ("missed_resource", r"\b(?:the|that)\s+(?:video|tutorial|reading|lecture|slides?)\b"
                        r"[^.?!]{0,60}\b(?:did ?n[o']t (?:cover|show|explain)|never (?:covered|showed)|"
                        r"broken|missing|would ?n[o']t (?:play|load))\b", False),

    ("time", r"\b(?:i|we)\s+(?:ran out of time|did ?n[o']t have (?:enough )?time|"
             r"was rushed|ran short on time)\b", False),
    ("time", r"\bif\s+i\s+had\s+(?:more|had)\s+time\b", False),
    ("confusion", r"(?:\bdid ?n[o']t|\bdidnt|\bdont|\bdo ?n[o']t|n[o']t|\bcouldn?[o']?t|\bcannot|\bcan ?n[o']t)\s+"
                  r"(?:really\s+)?(?:understand|get|follow|figure out|see how|know how)\b", True),
    ("confusion", r"\b(?:i|we)\s+(?:was|were|am|got|kept|felt)\s+(?:really\s+|very\s+|kind of\s+|"
                  r"a bit\s+|so\s+)?(?:confused|lost|stuck|unsure|uncertain|overwhelmed)\b", False),
    ("confusion", r"(?:\bnot|n[o']t)\s+(?:really\s+)?sure\s+(?:what|how|why|if|whether|where)\b", True),
    ("confusion", r"\b(?:confusing|unclear|ambiguous|vague)\b", True),
]

COMPILED = [(cat, re.compile(pat, re.I), fp) for cat, pat, fp in CUES]

FIRST_PERSON = re.compile(r"\b(?:i|i'?m|i'?ve|my|we|our|us|me)\b", re.I)

# A question the student is asking the instructor, not a rhetorical one inside an
# analysis. "Should I have..." and "Do we need..." are asked of a person.
ASKING = re.compile(r"\b(?:should|shall|do|does|did|can|could|would|is|are|was|were|will|may)\s+"
                    r"(?:i|we|my|our)\b|\b(?:i|we)\s+(?:was|were|am)\s+wondering\b", re.I)

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

CATEGORY_LABELS = {
    "confusion": "did not understand something",
    "instructions": "the instructions or rubric",
    "technical": "software or file trouble",
    "time": "ran out of time",
    "missed_resource": "could not find or use a resource",
    "question": "asked you a question",
}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.findall(text or "") if s.strip()]


def voice_in(text: str, *, source: str = "submission") -> list[dict]:
    """Sentences where a student is talking about their own experience.

    Returns at most a few per text: the aim is a readable list of what students
    said, not every sentence containing the word "confusing".
    """
    found: list[dict] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        if len(sentence) < 12 or len(sentence) > 400:
            continue
        key = sentence.lower()
        if key in seen:
            continue
        category = None
        for cat, pattern, needs_first_person in COMPILED:
            if not pattern.search(sentence):
                continue
            if needs_first_person and not FIRST_PERSON.search(sentence):
                continue
            category = cat
            break
        if (category is None and sentence.rstrip().endswith("?")
                and (ASKING.search(sentence) or FIRST_PERSON.search(sentence))):
            category = "question"
        if category:
            seen.add(key)
            found.append({"category": category, "text": sentence, "source": source})
    return found


def student_voice(entries: dict, prose_of) -> dict:
    """Collect what every student said, from their writing and their comments.

    `prose_of(entry)` supplies the student's own words: a .blend submission's
    extracted text is a report this tool wrote, and mining it would put the
    tool's phrasing in the students' mouths.
    """
    people: list[dict] = []
    counts: Counter = Counter()
    for uid, entry in (entries or {}).items():
        items = voice_in(prose_of(entry), source="submission")
        for comment in (entry.get("student_comments") or []):
            items += voice_in(str(comment.get("text") or ""), source="comment")
        if not items:
            continue
        # One student saying the same thing three ways is one voice, not three.
        trimmed, used = [], set()
        for item in items:
            if item["category"] in used and len(trimmed) >= 2:
                continue
            used.add(item["category"])
            trimmed.append(item)
        for item in trimmed:
            counts[item["category"]] += 1
        people.append({"user_id": str(uid), "name": entry.get("name") or str(uid),
                       "items": trimmed[:4]})
    people.sort(key=lambda p: -len(p["items"]))
    return {
        "n_students": len(people),
        "by_category": [{"category": cat, "label": CATEGORY_LABELS.get(cat, cat),
                         "count": n}
                        for cat, n in counts.most_common()],
        "students": people,
    }


# --------------------------------------------------------------- rubric health
CEILING = 0.95          # at or above this share of the points is effectively full
FLOOR = 0.25            # at or below this is effectively nothing
WEAK_MEAN = 0.70        # a row the class did not clear
LOW_SPREAD = 0.08       # stdev as a share of the points: nobody is separated
WEAK_LINK = 0.25        # correlation with the rest of the grade
MIN_N_FOR_R = 8         # below this a correlation is too unstable to act on


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    try:
        sx, sy = statistics.stdev(xs), statistics.stdev(ys)
    except statistics.StatisticsError:
        return None
    if not sx or not sy:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
    return round(cov / (sx * sy), 3)


def rubric_health(rubric: list[dict], rows: list[dict]) -> list[dict]:
    """Item analysis per criterion. `rows` is [{scores: {cid: pts}}, ...].

    Correlation is against the *rest* of the score rather than the whole, since a
    criterion is part of its own total and would otherwise look better connected
    than it is.
    """
    out = []
    if not rubric:
        return out
    # Defensive: a non-submission stored the old way carries per-criterion zeros,
    # and one of those in the input would pull every row's mean down. Callers are
    # expected to filter, but this is the function whose numbers get quoted back
    # to an instructor as "what your rubric did", so it filters too.
    rows = [row for row in rows if not is_unscored_marker(row)]
    for crit in rubric:
        cid = str(crit.get("id"))
        top = float(crit.get("points") or 0)
        values, rests = [], []
        for row in rows:
            scores = row.get("scores") or {}
            if cid not in scores:
                continue
            value = float(scores.get(cid) or 0)
            values.append(value)
            rests.append(sum(float(v or 0) for k, v in scores.items() if str(k) != cid))
        if not values or top <= 0:
            continue
        mean = statistics.fmean(values)
        spread = statistics.stdev(values) if len(values) > 1 else 0.0
        at_ceiling = sum(1 for v in values if v >= CEILING * top)
        at_floor = sum(1 for v in values if v <= FLOOR * top)
        item = {
            "id": cid, "label": crit.get("label") or cid, "points": top,
            "n": len(values), "mean": round(mean, 2),
            "mean_pct": round(100.0 * mean / top, 1),
            "spread": round(spread, 2),
            "spread_pct": round(100.0 * spread / top, 1),
            "min": round(min(values), 2), "max": round(max(values), 2),
            "at_ceiling": at_ceiling, "at_floor": at_floor,
            "r_with_rest": _pearson(values, rests),
            "findings": [],
        }
        share = mean / top
        if at_ceiling == len(values):
            item["findings"].append({
                "kind": "ceiling",
                "text": "Every student got full marks here, so this row did not "
                        "separate anyone. Either it is measuring something the "
                        "class has already mastered, or it is easier to satisfy "
                        "than you intended."})
        elif spread / top <= LOW_SPREAD and len(values) > 2:
            item["findings"].append({
                "kind": "no_spread",
                "text": f"Scores are nearly identical (spread of "
                        f"{item['spread_pct']}% of the points), so this row is "
                        f"not telling you who did better."})
        if share <= FLOOR:
            item["findings"].append({
                "kind": "floor",
                "text": "Almost nobody scored here. A whole class missing one row "
                        "usually means it was not taught, not practised, or the "
                        "row asks for something the prompt never asked for."})
        elif share < WEAK_MEAN:
            item["findings"].append({
                "kind": "weak",
                "text": f"The class averaged {item['mean_pct']}% on this row, "
                        f"the kind of gap that is worth a reteach rather than a "
                        f"curve on its own."})
        if (item["r_with_rest"] is not None
                and len(values) >= MIN_N_FOR_R
                and abs(item["r_with_rest"]) < WEAK_LINK
                and at_ceiling != len(values)):
            item["findings"].append({
                "kind": "weak_link",
                "text": f"Scores here barely track the rest of the grade "
                        f"(r={item['r_with_rest']}), so this row is measuring "
                        f"something the other rows are not. That can be exactly "
                        f"what you want, or a sign the wording is read "
                        f"differently by different students."})
        out.append(item)
    out.sort(key=lambda i: i["mean_pct"])
    return out


# ------------------------------------------------------------ course patterns
def normalise_label(label: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(label or "").lower()).strip()


def course_patterns(assignments: list[dict]) -> list[dict]:
    """Rubric rows that come out weak across more than one assignment.

    `assignments` is [{"name": str, "criteria": [{"label", "mean_pct"}]}]. Rows
    are matched on their wording, since rubric ids are per-assignment.
    """
    seen: dict[str, dict] = {}
    for item in assignments:
        for crit in item.get("criteria") or []:
            key = normalise_label(crit.get("label"))
            if not key:
                continue
            slot = seen.setdefault(key, {"label": crit.get("label"), "where": []})
            slot["where"].append({"assignment": item.get("name"),
                                  "mean_pct": crit.get("mean_pct")})
    out = []
    for slot in seen.values():
        weak = [w for w in slot["where"] if (w["mean_pct"] or 0) < 100 * WEAK_MEAN]
        if len(slot["where"]) < 2 or len(weak) < 2:
            continue
        out.append({
            "label": slot["label"],
            "times_seen": len(slot["where"]),
            "times_weak": len(weak),
            "mean_pct": round(statistics.fmean([w["mean_pct"] for w in weak]), 1),
            "where": sorted(weak, key=lambda w: w["mean_pct"] or 0),
        })
    out.sort(key=lambda p: (-p["times_weak"], p["mean_pct"]))
    return out
