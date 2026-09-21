"""Canvas file downloads follow the real redirect chain, and stop at a login."""
from __future__ import annotations

import io
import unittest
import urllib.error
from email.message import Message

from courseforge import canvas_policy
from courseforge.canvas import CanvasClient
from courseforge.grader import _file_name


CANVAS = "https://school.instructure.com"
INST = "https://inst-fs-iad-prod.inscloudgate.net/files/abc?token=1"
S3 = "https://instructure-uploads.s3.amazonaws.com/x"
REGIONAL = "https://instructure-uploads.s3.us-east-1.amazonaws.com/x?sig=1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _redirect(url, location, code=302):
    headers = Message()
    headers["Location"] = location
    return urllib.error.HTTPError(url, code, "Found", headers, io.BytesIO(b""))


class _Resp:
    def __init__(self, data, content_type="image/png"):
        self._data = data
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:n], self._data[n:]
        return out


class RedirectChain(unittest.TestCase):
    def test_regional_s3_is_a_canvas_file_host(self):
        self.assertTrue(canvas_policy.file_url_allowed(REGIONAL, CANVAS))
        self.assertTrue(canvas_policy.file_url_allowed(
            "https://s3.amazonaws.com/instructure-uploads/x", CANVAS))

    def test_three_redirects_land_on_the_file_without_the_token(self):
        client = CanvasClient(CANVAS, "token", scope="grading")
        seen = []

        def open_url(url, use_token, timeout, cookies=None):
            seen.append((url, use_token))
            if url.startswith(CANVAS):
                return _raise(_redirect(url, INST))
            if url == INST:
                return _raise(_redirect(url, S3))
            if url == S3:
                return _raise(_redirect(url, REGIONAL))
            if url == REGIONAL:
                return _Resp(PNG)
            raise AssertionError(url)

        client._open_url = open_url
        data = client._fetch_bytes(CANVAS + "/files/55/download?verifier=abc", timeout=5)
        self.assertEqual(data, PNG)
        self.assertTrue(seen[0][1], "the Canvas hop should carry the token")
        self.assertTrue(all(not token for _url, token in seen[1:]),
                        "the token must not leave the Canvas host")

    def test_a_login_redirect_is_not_saved_as_the_file(self):
        client = CanvasClient(CANVAS, "token", scope="grading")

        def open_url(url, use_token, timeout, cookies=None):
            return _raise(_redirect(url, CANVAS + "/login/canvas"))

        client._open_url = open_url
        with self.assertRaises(RuntimeError) as caught:
            client._fetch_bytes(CANVAS + "/files/55/download", timeout=5)
        self.assertIn("login", str(caught.exception))

    def test_a_plus_encoded_filename_is_readable(self):
        self.assertEqual(_file_name("Define+a+game.docx"), "Define_a_game.docx")


def _raise(exc):
    raise exc


if __name__ == "__main__":
    unittest.main()
