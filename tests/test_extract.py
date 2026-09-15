"""Embedded Canvas file links in submission HTML: only Canvas hosts are fetched."""
from __future__ import annotations

import unittest

from courseforge import canvas_policy
from courseforge.extract import rce_file_refs

BASE = "https://mgccc.instructure.com"


class RceFileRefsStayOnCanvas(unittest.TestCase):
    def test_evil_host_is_ignored(self):
        html = '<a href="https://evil.example/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_relative_file_is_resolved_to_the_canvas_host(self):
        html = '<a href="/files/99?verifier=abc">notes.docx</a>'
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file_id"], "99")
        self.assertTrue(refs[0]["url"].startswith(BASE + "/files/99/download"))
        self.assertIn("verifier=abc", refs[0]["url"])
        self.assertEqual(refs[0]["name"], "notes.docx")

    def test_canvas_absolute_url_is_kept(self):
        html = ('<a href="https://mgccc.instructure.com/users/7/files/42/'
                'preview?verifier=tok">Mover.cs</a>')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file_id"], "42")
        self.assertIn("mgccc.instructure.com", refs[0]["url"])
        self.assertTrue(refs[0]["url"].startswith("https://"))

    def test_http_is_refused(self):
        html = '<a href="http://mgccc.instructure.com/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_protocol_relative_off_host_is_ignored(self):
        html = '<a href="//evil.example/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_localhost_is_ignored(self):
        html = '<a href="https://127.0.0.1/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_rfc1918_is_ignored(self):
        html = '<a href="https://10.0.0.8/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_img_on_canvas_is_kept(self):
        html = ('<img src="https://mgccc.instructure.com/files/5/preview'
                '?verifier=v" alt="shot.png">')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["tag"], "img")
        self.assertEqual(refs[0]["name"], "shot.png")


class FileUrlAllowList(unittest.TestCase):
    def test_canvas_host_is_allowed(self):
        self.assertTrue(canvas_policy.file_url_allowed(
            BASE + "/files/1/download", BASE))

    def test_inscloudgate_is_allowed(self):
        self.assertTrue(canvas_policy.file_url_allowed(
            "https://inst-fs-iad-prod.inscloudgate.net/files/x", BASE))

    def test_metadata_ip_is_blocked(self):
        self.assertTrue(canvas_policy.is_blocked_host("169.254.169.254"))
        self.assertFalse(canvas_policy.file_url_allowed(
            "https://169.254.169.254/latest/meta-data", BASE))

    def test_userinfo_cannot_spoof_the_canvas_host(self):
        self.assertFalse(canvas_policy.file_url_allowed(
            "https://mgccc.instructure.com@127.0.0.1/files/1", BASE))


if __name__ == "__main__":
    unittest.main()
