"""Clear Ally's "Styles might be used instead of semantic markup for structure"
without the usual mistake of promoting everything to a heading (a port of
Fix-BoldAsStructure.ps1).

What trips the check: a paragraph whose entire content is one emphasis
element, <p><strong>...</strong></p> or <p><em>...</em></p>. Length is
irrelevant. Three different things hide behind that one flag:

    short structural label   -> promote to a real <h3>         promote_labels
    instruction sentence     -> drop the blanket <strong>       unbold_sentences
    code faked with &nbsp;   -> collapse into one code block    convert_code_runs

DEFAULT IS REPORT ONLY. `classify` sorts every hit into label / sentence /
code / unclassified so a person can triage; each remedy is opt-in.
promote_labels uses an ALLOWLIST, never a length rule, and refuses to promote
in a body with no <h2> (that would trade this flag for "skipped headings").
Every write is gated on the visible text being identical.
"""
from __future__ import annotations

import re
from collections import Counter

from . import restyle
from .workdir import kind_label, strip_theme

WHOLLY = re.compile(r"<p[^>]*>\s*<(strong|b|em|i)\b[^>]*>(.*?)</\1>\s*</p>", re.I | re.S)
INNER_EMPH = re.compile(r"^\s*<(em|i|strong|b)\b[^>]*>(.*)</\1>\s*$", re.I | re.S)
CODE_HINT = re.compile(r"[{};]|\breturn\b|\(\s*\)|\bpublic\s+\w|\bvoid\b")
DEFAULT_LABELS = (r"^You Do It \d+$", r"^Instructions:$", r"^HINTS:$", r"^SAMPLE:$",
                  r"^Sample output:$")
DEFAULT_SENTENCE_MIN = 40
CLASSES = ("label", "sentence", "code", "unclassified")


def h3_style() -> str:
    return "margin: 18px 0 8px; font-size: 17px; color: %s;" % restyle.NAVY


def code_style() -> str:
    # clean-look code block: bordered, monospace, NO background fill (a fill
    # would raise the "use of color" advisory)
    return ("margin-top: 12px; padding: 12px 14px; border-radius: 8px; border: 1px solid %s; "
            "font-family: %s; font-size: 13px; color: %s; white-space: pre-wrap; overflow-x: auto;"
            % (restyle.C["hairline"], restyle.F["mono"], restyle.C["body_text"]))


def vis(h: str) -> str:
    """The visible text as the PowerShell script measured it: tags gone,
    &nbsp; a space, whitespace collapsed. Entities otherwise kept."""
    h = re.sub(r"<[^>]+>", "", h or "")
    h = h.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", h).strip()


def looks_code(text: str) -> bool:
    return bool(CODE_HINT.search(text or ""))


def _compile(labels) -> list:
    out = []
    for rx in labels or DEFAULT_LABELS:
        try:
            out.append(re.compile(rx))
        except re.error:
            out.append(re.compile(re.escape(rx)))
    return out


def is_label(text: str, labels=None) -> bool:
    return any(rx.search(text or "") for rx in _compile(labels))


def classify(text: str, labels=None, sentence_min: int = DEFAULT_SENTENCE_MIN) -> str:
    if looks_code(text):
        return "code"
    if is_label(text, labels):
        return "label"
    if len(text) >= int(sentence_min):
        return "sentence"
    return "unclassified"


def hits(html: str) -> list:
    """Every wholly-emphasised paragraph with visible text, as (match, text)."""
    return [(m, vis(m.group(2))) for m in WHOLLY.finditer(html or "") if vis(m.group(2))]


def report_body(html: str, labels=None, sentence_min: int = DEFAULT_SENTENCE_MIN) -> list[dict]:
    return [{"text": t, "cls": classify(t, labels, sentence_min), "length": len(t)}
            for _, t in hits(html)]


# ---------------------------------------------------------------- remedies

def convert_code_runs(html: str) -> tuple[str, list[str]]:
    made: list[str] = []
    while True:
        ms = list(WHOLLY.finditer(html))
        run: list = []
        for m in ms:
            t = vis(m.group(2))
            if t and looks_code(t):
                if not run:
                    run = [m]
                else:
                    prev = run[-1]
                    between = html[prev.end():m.start()]
                    if re.match(r"^\s*$", between):
                        run.append(m)
                    else:
                        break
            elif run:
                break
        if not run:
            break
        lines = []
        for m in run:
            inner = re.sub(r"<br\s*/?>", "", m.group(2), flags=re.I)
            inner = inner.replace("&nbsp;", " ")
            inner = re.sub(r"<[^>]+>", "", inner)
            inner = inner.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            inner = inner.rstrip()
            lead = len(inner) - len(inner.lstrip(" "))
            lines.append(" " * min(lead, 24) + inner.lstrip())
        code = "\n".join(lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        block = '<div style="%s">%s</div>' % (code_style(), code)
        html = html[:run[0].start()] + block + html[run[-1].end():]
        made.append("code block (%d lines)" % len(lines))
    return html, made


def promote_labels(html: str, labels=None) -> tuple[str, list[str]]:
    made: list[str] = []
    if not re.search(r"<h2\b", html, re.I):
        made.append("SKIPPED promotion: body has no <h2>, an <h3> would be a skipped level")
        return html, made
    rxs = _compile(labels)
    while True:
        hit = None
        for m in WHOLLY.finditer(html):
            t = vis(m.group(2))
            if t and any(rx.search(t) for rx in rxs):
                hit = m
                break
        if not hit:
            break
        t = vis(hit.group(2))
        html = html[:hit.start()] + '<h3 style="%s">%s</h3>' % (h3_style(), t) + html[hit.end():]
        made.append("h3: " + t)
    return html, made


def unbold_sentences(html: str, labels=None,
                     sentence_min: int = DEFAULT_SENTENCE_MIN) -> tuple[str, list[str]]:
    made: list[str] = []
    while True:
        hit = None
        for m in WHOLLY.finditer(html):
            t = vis(m.group(2))
            if t and classify(t, labels, sentence_min) == "sentence":
                hit = m
                break
        if not hit:
            break
        inner = INNER_EMPH.sub(r"\2", hit.group(2))
        html = html[:hit.start()] + "<p>" + inner + "</p>" + html[hit.end():]
        t = vis(inner)
        made.append("unbolded: " + (t[:52] + "..." if len(t) > 52 else t))
    return html, made


def normalise_options(options: dict | None) -> dict:
    options = dict(options or {})
    labels = options.get("labels") or list(DEFAULT_LABELS)
    if isinstance(labels, str):
        labels = [ln.strip() for ln in labels.splitlines() if ln.strip()]
    return {
        "promote_labels": bool(options.get("promote_labels")),
        "unbold_sentences": bool(options.get("unbold_sentences")),
        "convert_code_runs": bool(options.get("convert_code_runs")),
        "labels": list(labels),
        "sentence_min": int(options.get("sentence_min") or DEFAULT_SENTENCE_MIN),
        "title_filter": options.get("title_filter") or "",
    }


def remedy(html: str, options: dict | None) -> tuple[str, list[str]]:
    """Apply the opted-in remedies. The caller gates on visible text."""
    o = normalise_options(options)
    out = strip_theme(html)
    made: list[str] = []
    if o["convert_code_runs"]:
        out, m = convert_code_runs(out)
        made += m
    if o["promote_labels"]:
        out, m = promote_labels(out, o["labels"])
        made += m
    if o["unbold_sentences"]:
        out, m = unbold_sentences(out, o["labels"], o["sentence_min"])
        made += m
    return out, made


# ------------------------------------------------------------ the sweep

def scan_course(client, course_id, options: dict | None = None, log=None) -> dict:
    """Read every body, classify the hits, compute the remedied body when a
    remedy is opted in. Nothing is written."""
    o = normalise_options(options)
    do_fix = o["promote_labels"] or o["unbold_sentences"] or o["convert_code_runs"]
    title_rx = re.compile(o["title_filter"]) if o["title_filter"] else None
    counts: Counter = Counter()
    items = []
    for entry in client.iter_content_bodies(course_id):
        body = entry.get("body") or ""
        title = entry.get("title") or ""
        if not body:
            continue
        if title_rx and not title_rx.search(title):
            continue
        found = report_body(body, o["labels"], o["sentence_min"])
        if not found:
            continue
        for h in found:
            counts[h["cls"]] += 1
        item = {"key": entry.get("key"), "kind": entry["kind"], "id": entry["id"],
                "title": title, "label": "%s: %s" % (kind_label(entry["kind"]), title),
                "published": entry.get("published"), "hits": found,
                "made": [], "would_change": False, "skipped_reason": "",
                "remaining": len(found)}
        if do_fix:
            new, made = remedy(body, o)
            real = [m for m in made if not m.startswith("SKIPPED")]
            item["made"] = made
            if real:
                if not restyle.same_reader_text(strip_theme(body), new):
                    item["skipped_reason"] = "visible text would change; left alone"
                else:
                    item["would_change"] = new != strip_theme(body)
                    item["before"] = strip_theme(body)
                    item["after"] = new
                    item["remaining"] = len(hits(new))
        items.append(item)
        if log:
            log("%s: %d hit(s)" % (item["label"], len(found)))
    changes = [it for it in items if it["would_change"] and not it["skipped_reason"]]
    return {"course_id": str(course_id), "options": o, "mode": "fix" if do_fix else "report",
            "items": items, "counts": {c: counts.get(c, 0) for c in CLASSES},
            "hit_count": sum(counts.values()), "change_count": len(changes),
            "changes": [{"key": it["key"], "label": it["label"], "made": it["made"],
                         "from": "%d hit(s)" % len(it["hits"]),
                         "to": "%d left" % it["remaining"]} for it in changes]}


def sentence(plan: dict, course_label: str) -> str:
    o = plan["options"]
    verbs = []
    if o["convert_code_runs"]:
        verbs.append("collapse bolded code into code blocks")
    if o["promote_labels"]:
        verbs.append("promote allowlisted labels to real h3 headings")
    if o["unbold_sentences"]:
        verbs.append("drop the blanket bold on instruction sentences")
    return ("Change the markup of %d items in %s to %s. Visible text is identical "
            "before and after in every item. Modules and publish state are not touched."
            % (plan["change_count"], course_label, " and ".join(verbs) or "nothing"))


def apply_plan(client, course_id, plan: dict, log=None) -> dict:
    """Write the remedied bodies from a plan. Each item is re-read first and
    skipped if it changed since the plan; each write is read back."""
    written, errors, skipped = [], [], []
    rows = [it for it in plan["items"] if it.get("would_change") and not it.get("skipped_reason")]
    first = True
    for i, it in enumerate(rows, 1):
        try:
            live = strip_theme(client.read_content_body(course_id, it["kind"], it["id"]))
        except Exception as exc:  # noqa: BLE001
            errors.append({"key": it["key"], "label": it["label"], "error": str(exc)[:300]})
            continue
        if live != it["before"]:
            skipped.append({"key": it["key"], "label": it["label"],
                            "reason": "changed in Canvas since the plan; run it again"})
            continue
        try:
            client.write_content_body(course_id, it["kind"], it["id"], it["after"])
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None)
            if first and status == 403:
                raise PermissionError(
                    "The first write (%s) was refused with 403, so the course is probably "
                    "write-locked. Nothing has been changed." % it["label"]) from exc
            first = False
            errors.append({"key": it["key"], "label": it["label"], "error": str(exc)[:300]})
            continue
        first = False
        rec = {"key": it["key"], "kind": it["kind"], "label": it["label"], "made": it["made"],
               "ok": True, "issues": []}
        try:
            back = strip_theme(client.read_content_body(course_id, it["kind"], it["id"]))
            if not restyle.same_reader_text(back, it["after"]):
                if not (it["kind"] == "quiz" and it.get("published") is False):
                    rec["issues"].append("visible text differs on the live course")
        except Exception as exc:  # noqa: BLE001
            rec["issues"].append("could not fetch it back: %s" % str(exc)[:160])
        rec["ok"] = not rec["issues"]
        written.append(rec)
        if log:
            log("%s %s" % ("changed" if rec["ok"] else "CHECK", it["label"]), i, len(rows))
    return {"written": written, "written_count": len(written), "errors": errors,
            "skipped": skipped, "live_fails": sum(1 for r in written if not r["ok"])}
