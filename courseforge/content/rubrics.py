"""Create real Canvas rubrics from definitions and attach them to assignments
(Push-CanvasRubrics.ps1).

Definitions, a JSON array:
    [{"assignment": "Final Submission" | 12345, "title": "Final project rubric",
      "use_for_grading": true,
      "criteria": [{"description": "...", "long_description": "...", "points": 40,
                    "ratings": [{"description": "Excellent", "points": 40}, ...]}]}]

Idempotent by title: an existing course rubric with the same title is updated
(criteria replaced) and its association re-pointed. Rubric DEFINITIONS only;
assessments are student data and the content client cannot reach them anyway.
"""
from __future__ import annotations

from typing import Callable

from .common import num, quiet_log


def _target(assignments: list[dict], ref) -> dict | None:
    ref_s = str(ref or "").strip()
    if ref_s.isdigit():
        return next((a for a in assignments if str(a.get("id")) == ref_s), None)
    return next((a for a in assignments if (a.get("name") or "").strip() == ref_s), None)


def plan(client, course_id, entries: list[dict]) -> dict:
    assignments = list(client.assignments_content(course_id))
    existing = {(r.get("title") or "").strip(): r for r in client.rubrics(course_id)}
    rows: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = e.get("title") or ""
        target = _target(assignments, e.get("assignment"))
        warnings: list[str] = []
        total = 0.0
        for c in e.get("criteria") or []:
            pts = num(c.get("points"))
            total += pts
            ratings = c.get("ratings") or []
            if not ratings:
                warnings.append(f"criterion '{c.get('description')}' has no ratings")
            else:
                top = max(num(r.get("points")) for r in ratings)
                if top != pts:
                    warnings.append(f"criterion '{c.get('description')}': max rating {top:g} != criterion points {pts:g}")
        if not title:
            mode = "skip"
            warnings.insert(0, "no title")
        elif target is None:
            mode = "skip"
            warnings.insert(0, f"assignment '{e.get('assignment')}' not found in this course")
        elif not e.get("criteria"):
            mode = "skip"
            warnings.insert(0, "no criteria")
        else:
            mode = "update" if title.strip() in existing else "create"
        rows.append({"title": title, "assignment": target.get("name") if target else str(e.get("assignment")),
                     "assignment_id": target.get("id") if target else None,
                     "assignment_points": target.get("points_possible") if target else None,
                     "rubric_id": existing.get(title.strip(), {}).get("id"),
                     "criteria": len(e.get("criteria") or []), "points": total,
                     "use_for_grading": bool(e.get("use_for_grading")), "action": mode,
                     "warnings": warnings})
    counts = {"create": sum(r["action"] == "create" for r in rows),
              "update": sum(r["action"] == "update" for r in rows),
              "skip": sum(r["action"] == "skip" for r in rows)}
    sentence = (f"Create {counts['create']} and update {counts['update']} rubric(s) and attach "
                f"each to its assignment. Grading with the rubric is turned on for "
                f"{sum(1 for r in rows if r['use_for_grading'] and r['action'] != 'skip')} of them. "
                "No student scores are touched.")
    return {"rows": rows, "counts": counts, "sentence": sentence,
            "keys": sorted(r["title"] for r in rows if r["action"] != "skip")}


def apply(client, course_id, entries: list[dict], log: Callable | None = None) -> dict:
    log = quiet_log(log)
    p = plan(client, course_id, entries)
    done: list[dict] = []
    mismatches: list[str] = []
    todo = [(row, e) for row, e in zip(p["rows"], [x for x in entries if isinstance(x, dict)])
            if row["action"] != "skip"]
    for i, (row, e) in enumerate(todo, 1):
        log(f"rubric '{row['title']}'", i - 1, len(todo))
        criteria = []
        for c in e.get("criteria") or []:
            criteria.append({"description": c.get("description", ""),
                             "long_description": c.get("long_description", ""),
                             "points": num(c.get("points")),
                             "ratings": [{"description": r.get("description", ""),
                                          "long_description": r.get("long_description", ""),
                                          "points": num(r.get("points"))} for r in c.get("ratings") or []]})
        association = {"type": "Assignment", "id": row["assignment_id"],
                       "use_for_grading": bool(e.get("use_for_grading")), "purpose": "grading"}
        if row["action"] == "update":
            resp = client.update_rubric(course_id, row["rubric_id"], row["title"], criteria, association)
        else:
            resp = client.create_rubric(course_id, row["title"], criteria, association)
        rubric = resp.get("rubric") if isinstance(resp, dict) and isinstance(resp.get("rubric"), dict) else resp
        rid = (rubric or {}).get("id")
        try:
            back = client.rubric(course_id, rid)
            if (back.get("title") or "").strip() != row["title"].strip():
                mismatches.append(f"'{row['title']}': Canvas kept the title '{back.get('title')}'")
            data = back.get("data") or back.get("criteria") or []
            if data and len(data) != len(criteria):
                mismatches.append(f"'{row['title']}': {len(data)} criteria read back, {len(criteria)} sent")
        except Exception as exc:  # noqa: BLE001
            mismatches.append(f"'{row['title']}': could not read the rubric back ({exc})")
        done.append({"title": row["title"], "id": rid, "assignment_id": row["assignment_id"],
                     "action": row["action"]})
        log(f"{row['action']}d rubric '{row['title']}' on '{row['assignment']}'")
    log("done", len(todo), len(todo))
    return {"rubrics": done, "mismatches": mismatches, "count": len(done),
            "skipped": [r for r in p["rows"] if r["action"] == "skip"]}
