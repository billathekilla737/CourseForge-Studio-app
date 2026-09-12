"""Textual overlap between submissions. Deterministic, local, no model call.

Finds word sequences that two submissions share, after subtracting the text
everybody shares (the assignment prompt, a supplied template, a quoted rubric),
and reports the overlapping passages themselves so the instructor can read them
and judge.

This measures overlap. It is not a cheating detector, and nothing in here should
ever be presented as a finding of misconduct: students who used the same source,
worked together where that was allowed, or answered a narrow prompt about a
single reading will overlap legitimately. The output is a reading list, ordered
by how much two submissions have in common.
"""
from __future__ import annotations

import math
import re

WORD = re.compile(r"[a-z0-9']+")

# Six-word windows: long enough that ordinary phrasing does not collide, short
# enough to survive a student reordering a clause.
DEFAULT_K = 6

# A pair is worth a closer look at roughly two sentences of verbatim overlap, or
# a sixth of the shorter submission in common. Below that, shared phrasing is
# normal: a quoted definition, a term of art, a sentence from the reading.
NOTABLE_WORDS = 20
NOTABLE_CONTAINMENT = 0.15
# Containment is a ratio, so two short submissions whose only common text is one
# quoted sentence can score high on it. Require a real amount of shared text
# before the ratio alone is allowed to flag a pair.
NOTABLE_WINDOWS = 10


# Part kinds that the student actually wrote. Everything else in an extracted
# entry is produced by this tool (a Blender scene report, an extraction error)
# or by a machine, and is identical across submissions by construction.
PROSE_KINDS = ("text",)


def student_prose(entry: dict) -> str:
    """Only the text the student wrote, for comparison purposes.

    A .blend submission's extracted text is a scene report this tool generated:
    every student gets near-identical wording, so comparing it would flag a
    whole class for "overlap" that none of them typed. When an entry has parts,
    trust the part kinds; when it has none (a plain online text entry), the
    whole text is the student's.
    """
    parts = entry.get("parts")
    if isinstance(parts, list) and parts:
        prose = [str(part.get("text") or "") for part in parts
                 if isinstance(part, dict) and part.get("kind") in PROSE_KINDS]
        return "\n\n".join(chunk for chunk in prose if chunk.strip())
    return str(entry.get("text") or "")


def tokens(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def shingles(toks: list[str], k: int) -> list[tuple[str, ...]]:
    if len(toks) < k:
        return []
    return [tuple(toks[i:i + k]) for i in range(len(toks) - k + 1)]


def _masked_shingles(toks: list[str], drop: set, k: int) -> list[tuple | None]:
    """Shingle a token stream with the spans covered by `drop` blanked out.

    Subtracting shared windows from a set is not enough on its own: a window
    straddling the last words of the assignment prompt and the first words of a
    student's own sentence belongs to neither, and matches between two students
    purely because both were answering the same prompt. Masking the covered
    token positions removes those junction artifacts.
    """
    if not drop:
        return list(shingles(toks, k))
    covered = bytearray(len(toks))
    for i, sh in enumerate(shingles(toks, k)):
        if sh in drop:
            for j in range(i, i + k):
                covered[j] = 1
    out: list[tuple | None] = []
    for i in range(max(0, len(toks) - k + 1)):
        out.append(None if any(covered[i:i + k]) else tuple(toks[i:i + k]))
    return out


def _runs(shared: set, b_shingles: list, b_toks: list[str], k: int,
          limit: int = 4) -> list[dict]:
    """Merge consecutive shared windows back into readable passages."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i, sh in enumerate(b_shingles):
        if sh is not None and sh in shared:
            if start is None:
                start = i
        elif start is not None:
            spans.append((start, i - 1 + k))
            start = None
    if start is not None:
        spans.append((start, len(b_shingles) - 1 + k))
    spans.sort(key=lambda s: s[1] - s[0], reverse=True)
    return [{"words": end - begin, "text": " ".join(b_toks[begin:end])}
            for begin, end in spans[:limit]]


def compare(texts: dict[str, str], boilerplate: str = "",
            k: int = DEFAULT_K) -> dict:
    """Compare every pair in `texts` (keyed by whatever id the caller uses).

    Returns {"k", "pairs", "skipped", "common_dropped"}. `pairs` is sorted with
    the most overlapping first; each carries the passages themselves.
    """
    toks = {key: tokens(text) for key, text in texts.items()}
    skipped = [key for key, t in toks.items() if len(t) < k]
    keys = [key for key in texts if len(toks[key]) >= k]

    # Pass one: the assignment prompt, the instructions, a supplied template.
    common: set = set(shingles(tokens(boilerplate), k))
    shing = {key: _masked_shingles(toks[key], common, k) for key in keys}

    # Pass two: whatever the group still shares is the assignment, not the
    # student -- a pasted rubric, a citation everyone used, a class template.
    if len(keys) > 2:
        cap = max(2, math.ceil(0.3 * len(keys)))
        frequency: dict[tuple, int] = {}
        for key in keys:
            for sh in set(shing[key]):
                if sh is not None:
                    frequency[sh] = frequency.get(sh, 0) + 1
        group = {sh for sh, n in frequency.items() if n > cap}
        if group:
            common |= group
            shing = {key: _masked_shingles(toks[key], common, k) for key in keys}

    sets = {key: {sh for sh in shing[key] if sh is not None} for key in keys}

    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            set_a, set_b = sets[a], sets[b]
            if not set_a or not set_b:
                continue
            shared = set_a & set_b
            if not shared:
                continue
            union = len(set_a | set_b)
            passages = _runs(shared, shing[b], toks[b], k)
            longest = passages[0]["words"] if passages else 0
            containment = len(shared) / min(len(set_a), len(set_b))
            pairs.append({
                "a": a, "b": b,
                "shared_windows": len(shared),
                "jaccard": round(len(shared) / union, 4) if union else 0.0,
                "containment": round(containment, 4),
                "longest_words": longest,
                "passages": passages,
                "notable": (longest >= NOTABLE_WORDS
                            or (containment >= NOTABLE_CONTAINMENT
                                and len(shared) >= NOTABLE_WINDOWS)),
            })

    pairs.sort(key=lambda p: (p["longest_words"], p["containment"]), reverse=True)
    # `pairs` holds only the pairs with something in common; `compared` is how
    # many were actually measured, which is what a report should quote.
    return {"k": k, "pairs": pairs, "skipped": skipped,
            "compared": len(keys) * (len(keys) - 1) // 2,
            "common_dropped": len(common)}
