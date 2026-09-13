"""Cheap guards on the files the browser loads.

There is no JavaScript test runner here and adding one for six files would cost
more than it saves, so this pins the two things that have actually gone wrong
while editing them with tools rather than by hand: a stray control byte written
into the source, and a backslash lost out of a string that becomes a regular
expression.

Neither is caught by reading the diff. Both break the page silently.
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web"


def web_files(*suffixes):
    return [p for p in WEB.rglob("*")
            if p.is_file() and p.suffix in suffixes and "vendor" not in p.parts]


class NoStrayBytes(unittest.TestCase):
    def test_every_file_is_utf8(self):
        for path in web_files(".js", ".css", ".html"):
            with self.subTest(file=path.name):
                path.read_bytes().decode("utf-8")

    def test_no_control_characters(self):
        """A NUL written into a source file makes the browser refuse the whole
        script, and the page then loads with one area silently missing."""
        allowed = {0x09, 0x0A, 0x0D}
        for path in web_files(".js", ".css", ".html"):
            raw = path.read_bytes()
            bad = sorted({b for b in raw if b < 0x20 and b not in allowed})
            with self.subTest(file=path.name):
                self.assertEqual(bad, [], f"control bytes {bad} in {path.name}")


class RegexStrings(unittest.TestCase):
    """A regex built from a string needs its backslashes doubled.

    `join('\\s+')` is the separator that lets a name repair replace "Jordan
    Vancc" with "Jordan Vance" rather than "Jordan Jordan Vance". Written with
    one backslash it is the literal "s+", the pattern never matches, and the
    only symptom is a duplicated first name in the composer.
    """

    def test_the_name_repair_joins_on_a_real_whitespace_class(self):
        src = (WEB / "js" / "assistant.js").read_text(encoding="utf-8")
        self.assertIn("function repairName", src, "the helper was renamed")
        self.assertIn(r"join('\\s+')", src,
                      r"the separator must be '\\s+' in the source, so the "
                      r"built pattern is \s+ and not the letter s")
        self.assertNotIn(r"join('\s+')", src.replace(r"join('\\s+')", ""))

    def test_no_single_backslash_escapes_survive_in_built_patterns(self):
        """Anything handed to `new RegExp` from a literal in these files has to
        survive JavaScript's own string escaping first."""
        for path in web_files(".js"):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"new RegExp\(([^)]{0,200})", src):
                arg = m.group(1)
                # A lone \s \d \b \w \p inside a single-quoted piece is the
                # mistake; the doubled form is what is wanted.
                stray = re.findall(r"(?<!\\)\\[sdbwp](?![a-z])", arg.replace(r"\\", ""))
                with self.subTest(file=path.name, arg=arg[:60]):
                    self.assertEqual(stray, [], f"single-escaped {stray} in {path.name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheShellNamesWhereYouAre(unittest.TestCase):
    """Three small things the review found missing from the shell."""

    def test_the_page_has_an_icon(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html)
        self.assertTrue((WEB / "favicon.svg").is_file())

    def test_every_route_resets_the_tab_title(self):
        """Leaving a course for the list kept the course name in the tab."""
        src = (WEB / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("document.title = TITLES[parts[0]] || 'CourseForge Studio';", src)

    def test_the_inbox_view_has_a_heading_to_land_on(self):
        """Skip to content and the after-route focus knew every view but the
        one Inbox and Reports share, so neither could be reached by keyboard."""
        src = (WEB / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("inbox: '#viewInbox h2'", src)
        self.assertIn("'#viewInbox', '#viewWork'", src)
