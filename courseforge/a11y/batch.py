"""Run the HTML pipeline (fetch -> restyle -> verify -> push) across several
courses in one go (a port of Batch-Remediate.ps1): the curriculum designer's
actual job.

Posture: every course is fetched, restyled and verified first, with nothing
written. A course whose verify fails is skipped entirely. Only then, and only
with apply, does one confirm gate cover the whole batch, and every course is
pushed with the same guards as a single push. One summary lands in
data/batch/batch-summary.json.

The `ctx` object needs `.content` (the content-scoped Canvas client),
`.course_dir(cid)`, `.cfg` and either `.data_root` or `.store.root`. The
server's App satisfies it; the CLI builds a small stand-in.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import ledger
from . import dump, push, restyle
from .workdir import (LOOKS, course_label, load_fixes, load_manifest,
                      load_report, now_iso, workdir)

FRESH_S = 20 * 60      # a passing verify this recent is reused on the confirm retry


class BatchSkip(Exception):
    pass


def _data_root(ctx) -> Path:
    root = getattr(ctx, "data_root", None)
    if root is None:
        root = ctx.store.root
    return Path(root)


def summary_path(ctx) -> Path:
    return _data_root(ctx) / "batch" / "batch-summary.json"


def last_summary(ctx) -> dict | None:
    path = summary_path(ctx)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _age_s(iso: str | None) -> float:
    if not iso:
        return 1e12
    try:
        when = datetime.fromisoformat(iso)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - when).total_seconds()
    except ValueError:
        return 1e12


def _fresh(wd, look) -> bool:
    """A recent dump already restyled with this look and verified clean. Used
    so the second pass (with the confirm token) does not refetch everything."""
    m = load_manifest(wd)
    if not m or m.get("look") != look or m.get("verify_fails") not in (0,):
        return False
    if load_report(wd) is None:
        return False
    return _age_s(m.get("dumped_at")) < FRESH_S


def _log(log, message, done=None, total=None):
    if log:
        try:
            log(message, done, total)
        except TypeError:
            log(message)


def run(ctx, course_ids, look: str = "clean", apply: bool = False, log=None,
        gate=None, stop_on_error: bool = False) -> dict:
    """gate(payload, sentence, detail) is called once before the first write
    when `apply` is set; the route wraps app._gate, the CLI wraps confirm_apply."""
    if look not in LOOKS:
        raise ValueError("unknown look %r" % (look,))
    course_ids = [str(c).strip() for c in course_ids if str(c).strip()]
    if not course_ids:
        raise ValueError("no course ids given")
    rows: list[dict] = []
    plans: dict[str, dict] = {}
    total = len(course_ids)
    for n, cid in enumerate(course_ids, 1):
        row = {"course": cid, "name": "", "items": 0, "styled": 0, "wrapped": 0,
               "skipped": 0, "fills": 0, "verify": "-", "push": "-", "error": ""}
        rows.append(row)
        try:
            wd = workdir(ctx.course_dir(cid))
            label = course_label(ctx, cid)
            if _fresh(wd, look):
                _log(log, "course %s: reusing the verified restyle from a moment ago" % cid, n, total)
                v = {"fails": 0}
            else:
                _log(log, "course %s: fetching" % cid, n, total)
                dump.dump(ctx.content, cid, wd, course_label=label,
                          base_url=getattr(ctx.cfg, "base_url", ""))
                _log(log, "course %s: restyling (%s look)" % (cid, look), n, total)
                restyle.transform(wd, look)
                _log(log, "course %s: verifying" % cid, n, total)
                v = restyle.verify(wd)
            m = load_manifest(wd) or {}
            row["name"] = m.get("course_label") or label
            row["items"] = len(m.get("items", []))
            for it in m.get("items", []):
                note = it.get("transform_note")
                if note == "styled":
                    row["styled"] += 1
                elif note == "wrapped":
                    row["wrapped"] += 1
                elif note == "skipped-empty":
                    row["skipped"] += 1
                row["fills"] += int(it.get("fills_added") or 0)
            row["verify"] = "PASS" if v["fails"] == 0 else "FAIL(%d)" % v["fails"]
            if v["fails"]:
                raise BatchSkip("verify failed; push skipped")
            p = push.plan(wd, exclude=load_fixes(wd).get("excluded"))
            plans[cid] = p
            row["push"] = "would write %d" % p["count"]
            row["plan_count"] = p["count"]
            row["phrase"] = p["phrase"]
        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)[:400]
            _log(log, "course %s: %s" % (cid, row["error"]), n, total)
            if stop_on_error:
                break

    summary = {"ran_at": now_iso(), "look": look, "applied": False,
               "course_ids": course_ids, "rows": rows,
               "total_fills": sum(r["fills"] for r in rows),
               "total_planned": sum(int(r.get("plan_count") or 0) for r in rows)}

    if apply and plans:
        total_items = sum(p["count"] for p in plans.values())
        if total_items:
            names = ", ".join((r["name"] or r["course"]) for r in rows if r["course"] in plans)
            sentence = ("Replace the bodies of %d items across %d courses (%s) with the "
                        "restyled versions (%s look). Visible text is verified unchanged in "
                        "every course. Modules and publish state are not touched. Each course "
                        "keeps its previous bodies here and can be restored on its own."
                        % (total_items, len(plans), names, look))
            payload = {"course_ids": sorted(plans), "look": look,
                       "keys": {cid: sorted(r["key"] for r in p["rows"]) for cid, p in plans.items()}}
            detail = json.dumps([{"label": (r["name"] or r["course"]), "from": "current bodies",
                                  "to": r.get("phrase") or "%d items" % r.get("plan_count", 0)}
                                 for r in rows if r["course"] in plans])
            if gate is not None:
                gate(payload, sentence, detail)
            for cid, p in plans.items():
                row = next(r for r in rows if r["course"] == cid)
                wd = workdir(ctx.course_dir(cid))
                try:
                    _log(log, "course %s: pushing %s" % (cid, p["phrase"]))
                    result = push.push(ctx.content, cid, wd, log=log,
                                       exclude=load_fixes(wd).get("excluded"))
                    row["push"] = "WRITTEN %d" % result["written_count"]
                    if result["errors"] or result["live_fails"]:
                        row["push"] += " (%d error(s), %d live check(s) failed)" % (
                            len(result["errors"]), result["live_fails"])
                    ledger.record(ctx.course_dir(cid), "a11y",
                                  "Replaced the bodies of %s with restyled versions (%s look), "
                                  "as part of a batch run" % (result["phrase"], look),
                                  url="%s/courses/%s/pages" % (getattr(ctx.cfg, "base_url", ""), cid),
                                  count=result["written_count"], kind="a11y.push",
                                  undo={"route": "/a11y/%s/html/restore" % cid, "body": {}})
                except Exception as exc:  # noqa: BLE001
                    row["error"] = str(exc)[:400]
                    row["push"] = "FAILED"
                    _log(log, "course %s: %s" % (cid, row["error"]))
            summary["applied"] = True
    summary["ran_at"] = now_iso()
    path = summary_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    summary["path"] = str(path)
    return summary


def table(summary: dict) -> str:
    cols = ("course", "name", "items", "styled", "wrapped", "skipped", "fills", "verify", "push", "error")
    rows = [[str(r.get(c, "")) for c in cols] for r in summary.get("rows", [])]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    out = [line, "  ".join("-" * w for w in widths)]
    for r in rows:
        out.append("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)))
    return "\n".join(out)
