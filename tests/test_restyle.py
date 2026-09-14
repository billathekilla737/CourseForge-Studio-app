"""test_restyle_html.py (courseforge) - regression harness for the restyler.

restyle_html.py's `verify` is the gate Push-CanvasRemediation.ps1 trusts before
it overwrites bodies in a LIVE Canvas course. Nothing tested it. These are the
properties that gate is actually claiming, each as a test that fails loudly:

  1. visible text is never altered (the whole premise)
  2. links and images are never added, dropped or repointed
  3. output is pure ASCII (the Canvas raw-emoji 500)
  4. an unstructured body gains a real <h2> (the Ally "missing heading" fix)
  5. transform is idempotent - running it twice changes nothing further
  6. clean look leaves no background fill and no orphaned light text
  7. verify FAILS when a styled file is tampered with (it must not rubber-stamp)
  8. verify records a digest per file, so a stale report is detectable
  9. an empty body is skipped, never wrapped into a fake page

Run:  python test_restyle_html.py          (exit 0 = all good)
No pytest dependency on purpose: this has to run on a school machine with
nothing installed but Python.

NOTE: this is the ONE file in the toolkit that is deliberately not ASCII-only.
Testing the entity encoder needs real non-ASCII input (smart quotes, accents,
an emoji), so UNICODE_BODY and one assertion below contain literal non-ASCII
characters on purpose. Everything else stays ASCII.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from courseforge.a11y import restyle as R  # noqa: E402


# --------------------------------------------------------------- fixtures

TEMPLATED = (
    '<div style="max-width: 980px; margin: 0 auto; font-family: Inter, '
    "'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.55;\">"
    '<div style="padding: 24px; border-radius: 8px; border-top: 5px solid #E9A821;">'
    '<div style="font-size: 13px;">IMT 1213 &middot; Week 6</div>'
    '<h2 style="margin: 6px 0 4px; font-size: 30px; color: #061E3F;">'
    'Player Motivation</h2>'
    '<p style="margin: 0;">What drives players.</p></div>'
    '<div style="margin-top: 18px; padding: 18px; border: 1px solid #d7dce3; '
    'border-top: 4px solid #E9A821;">'
    '<h3 style="color: #061E3F;">Why players play</h3>'
    '<p>Autonomy, competence and relatedness. '
    '<a href="https://example.edu/sdt">Read the theory</a>.</p>'
    '<img src="https://example.edu/flow.png" alt="The flow channel diagram">'
    '</div></div>')

UNSTRUCTURED = (
    "<p>Welcome to the course. Read the syllabus before Friday, then post "
    "your introduction in the week one discussion. Office hours are Tuesday "
    "and Thursday afternoons, and the best way to reach me is Canvas "
    "Inbox. Late work follows the policy in the syllabus, which allows a "
    "single 48-hour extension per term with no penalty if you ask before the "
    "deadline rather than after it.</p>"
    '<p>Textbook: <a href="https://example.edu/book">the open text</a>.</p>')

EMPTY = "<p>&nbsp;</p>"

UNICODE_BODY = (
    "<p>Café naïve — “smart quotes”, an em dash, "
    "éèê, and an emoji \U0001f600 that has 500'd Canvas "
    "before. This paragraph is long enough to trip the missing-heading rule "
    "so the wrapper path runs and the entity encoding gets exercised end to "
    "end on real non-ASCII content.</p>")


def build_workdir(bodies, look="clean"):
    """A minimal Dump-CanvasContent.ps1 workdir: manifest.json + bodies/."""
    wd = tempfile.mkdtemp(prefix="cf-restyle-")
    os.makedirs(os.path.join(wd, "bodies"))
    items = []
    for i, (kind, name, html_body) in enumerate(bodies, 1):
        fn = "%s_%d.html" % (kind, i)
        with open(os.path.join(wd, "bodies", fn), "w", encoding="utf-8") as f:
            f.write(html_body)
        items.append({"kind": kind, "id": i, "name": name,
                      "slug": name.lower().replace(" ", "-"),
                      "file": os.path.join(wd, "bodies", fn)})
    manifest = {"course_id": "999999", "course_label": "TEST 1000 Course",
                "look": look, "items": items}
    with open(os.path.join(wd, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return wd


def read_manifest(wd):
    with open(os.path.join(wd, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ tests

FAILURES = []


def check(cond, label, detail=""):
    if cond:
        print("  PASS %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        FAILURES.append(label)


def test_text_and_links_preserved(look):
    wd = build_workdir([("Page", "Week 6 Lesson", TEMPLATED),
                        ("Page", "Start Here", UNSTRUCTURED)], look=look)
    try:
        R.cmd_transform(wd, look)
        m = read_manifest(wd)
        for it in m["items"]:
            with open(it["file"], encoding="utf-8") as f:
                orig = f.read()
            with open(it["styled_file"], encoding="utf-8") as f:
                new = f.read()
            vo, vn = R.visible_text(orig), R.visible_text(new)
            if it["transform_note"] == "styled":
                check(vo == vn, "[%s] %s: visible text identical" % (look, it["name"]),
                      "\n     was: %r\n     now: %r" % (vo[:90], vn[:90]))
            else:
                check(vo and vo in vn,
                      "[%s] %s: original text preserved inside wrapper" % (look, it["name"]))
            check(R.attr_set(orig, "href") == R.attr_set(new, "href"),
                  "[%s] %s: links unchanged" % (look, it["name"]))
            check(R.attr_set(orig, "src") == R.attr_set(new, "src"),
                  "[%s] %s: images unchanged" % (look, it["name"]))
            check(all(ord(c) < 128 for c in new),
                  "[%s] %s: output is pure ASCII" % (look, it["name"]))
        fails = R.cmd_verify(wd)
        check(fails == 0, "[%s] verify passes its own output" % look,
              "(%d failures)" % fails)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_heading_added():
    wd = build_workdir([("Page", "Start Here", UNSTRUCTURED)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        it = read_manifest(wd)["items"][0]
        with open(it["styled_file"], encoding="utf-8") as f:
            new = f.read()
        check("<h2" in new.lower(), "unstructured body gains a real <h2>")
        check("no semantic heading" not in " ".join(R.a11y_issues(new)),
              "missing-heading a11y issue is resolved")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_idempotent():
    for look in ("clean", "hybrid", "rich"):
        wd = build_workdir([("Page", "Week 6 Lesson", TEMPLATED)], look=look)
        try:
            R.cmd_transform(wd, look)
            first = open(read_manifest(wd)["items"][0]["styled_file"],
                         encoding="utf-8").read()
            R.cmd_transform(wd, look)
            second = open(read_manifest(wd)["items"][0]["styled_file"],
                          encoding="utf-8").read()
            check(first == second, "[%s] transform is idempotent" % look,
                  "second pass changed %d chars" % abs(len(first) - len(second)))
        finally:
            shutil.rmtree(wd, ignore_errors=True)


def test_clean_has_no_fills():
    wd = build_workdir([("Page", "Week 6 Lesson", TEMPLATED),
                        ("Page", "Start Here", UNSTRUCTURED)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        for it in read_manifest(wd)["items"]:
            with open(it["styled_file"], encoding="utf-8") as f:
                new = f.read()
            check("background:" not in new.replace(" ", "").lower()
                  or "background" not in new.lower(),
                  "clean look leaves no background fill on %s" % it["name"])
            offenders = []
            for mm in R.OPEN_TAG.finditer(new):
                tag = mm.group(1).lower()
                if tag in ("h2", "h3", "a"):
                    continue
                st = R.re.search(r'style\s*=\s*"([^"]*)"', mm.group(2), R.re.I)
                if st and R.COLOR_DECL.search(st.group(1)):
                    offenders.append(tag)
            check(not offenders,
                  "clean look leaves no coloured non-heading text on %s" % it["name"],
                  "offending tags: %s" % offenders[:5])
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_verify_catches_tampering():
    wd = build_workdir([("Page", "Week 6 Lesson", TEMPLATED)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        it = read_manifest(wd)["items"][0]
        with open(it["styled_file"], encoding="utf-8") as f:
            good = f.read()
        # 1. reworded prose must be caught (this is the whole premise)
        with open(it["styled_file"], "w", encoding="utf-8") as f:
            f.write(good.replace("Autonomy", "Autonomy is optional"))
        check(R.cmd_verify(wd) > 0, "verify catches altered visible text")
        # 2. a repointed link must be caught
        with open(it["styled_file"], "w", encoding="utf-8") as f:
            f.write(good.replace("https://example.edu/sdt", "https://evil.test/x"))
        check(R.cmd_verify(wd) > 0, "verify catches a repointed link")
        # 3. a dropped image must be caught
        with open(it["styled_file"], "w", encoding="utf-8") as f:
            f.write(good.replace('<img src="https://example.edu/flow.png" '
                                 'alt="The flow channel diagram">', ""))
        check(R.cmd_verify(wd) > 0, "verify catches a dropped image")
        # 4. raw non-ASCII must be caught
        with open(it["styled_file"], "w", encoding="utf-8") as f:
            f.write(good.replace("players", "plàyers"))
        check(R.cmd_verify(wd) > 0, "verify catches raw non-ASCII")
        # restore: a clean file must pass again
        with open(it["styled_file"], "w", encoding="utf-8") as f:
            f.write(good)
        check(R.cmd_verify(wd) == 0, "verify passes again once restored")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_verify_report_carries_digests():
    wd = build_workdir([("Page", "Week 6 Lesson", TEMPLATED)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        R.cmd_verify(wd)
        with open(os.path.join(wd, "verify-report.json"), encoding="utf-8") as f:
            report = json.load(f)
        check(all(r.get("styled_sha256") and r.get("styled_file") for r in report),
              "verify-report records a digest per file (stale-report guard)")
        it = read_manifest(wd)["items"][0]
        with open(it["styled_file"], "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        rec = [r for r in report if r["styled_file"] == it["styled_file"]][0]
        check(rec["styled_sha256"] == actual,
              "recorded digest matches the file on disk")
        # and the digest must MOVE when the file changes, or the guard is a no-op
        with open(it["styled_file"], "a", encoding="utf-8") as f:
            f.write("<!-- touched -->")
        with open(it["styled_file"], "rb") as f:
            after = hashlib.sha256(f.read()).hexdigest()
        check(after != rec["styled_sha256"],
              "digest changes when the styled file changes")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_empty_body_skipped():
    wd = build_workdir([("Page", "Blank Page", EMPTY)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        it = read_manifest(wd)["items"][0]
        check(it.get("transform_note") == "skipped-empty",
              "empty body is skipped, not wrapped into a fake page",
              "got %r" % it.get("transform_note"))
        check(not it.get("styled_file"),
              "empty body produces no styled file to push")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_unicode_is_entity_encoded():
    wd = build_workdir([("Page", "Unicode Page", UNICODE_BODY)], look="clean")
    try:
        R.cmd_transform(wd, "clean")
        it = read_manifest(wd)["items"][0]
        with open(it["styled_file"], encoding="utf-8") as f:
            new = f.read()
        check(all(ord(c) < 128 for c in new),
              "emoji/smart quotes survive as ASCII entities")
        import html as _h
        check("\U0001f600" in _h.unescape(new),
              "the emoji is preserved (encoded, not deleted)")
        check(R.cmd_verify(wd) == 0, "verify passes on a unicode body")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def test_instructional_background_text_is_not_eaten():
    html = ('<p>Set background: #fff on the body. Then save.</p>'
            '<p style="background: #061e3f; color: #fff">Hero</p>')
    out, n = R.strip_fills(html)
    check("Set background: #fff on the body" in out,
          "instructional CSS in the body is not a style to strip")
    style = R.re.search(r'style="([^"]*)"', out)
    check(style is not None and "background:" not in style.group(1).lower(),
          "style-attribute fills are still stripped")
    check(n >= 1, "counted the style-attribute fill")


def test_brand_config_is_honoured():
    """brand.json must actually drive emitted colour, or it is decoration."""
    check(R.NAVY == R.C["navy"] and R.GOLD == R.C["gold"],
          "brand palette feeds the module constants")
    check(R.C["hairline"] in R.CARD_OPEN and R.F["display"] in R.HERO_CLEAN,
          "emitted markup uses brand colours and fonts")


def main():
    print("restyle_html regression harness")
    print("-" * 60)
    for look in ("clean", "hybrid", "rich"):
        test_text_and_links_preserved(look)
    test_heading_added()
    test_idempotent()
    test_clean_has_no_fills()
    test_verify_catches_tampering()
    test_verify_report_carries_digests()
    test_empty_body_skipped()
    test_unicode_is_entity_encoded()
    test_instructional_background_text_is_not_eaten()
    test_brand_config_is_honoured()
    print("-" * 60)
    if FAILURES:
        print("RESTYLE TESTS FAIL (%d)" % len(FAILURES))
        for f in FAILURES:
            print("   %s" % f)
        return 1
    print("RESTYLE TESTS PASS")
    return 0


# So `python -m unittest tests.test_restyle` runs the same harness.
import unittest  # noqa: E402


class RestyleHarness(unittest.TestCase):
    def test_harness_passes(self):
        del FAILURES[:]
        self.assertEqual(main(), 0, FAILURES)


if __name__ == "__main__":
    sys.exit(main())
