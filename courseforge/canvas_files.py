"""Course files for CanvasClient: list, download safely, upload in place.

Mixed into CanvasClient (see canvas.py).

Three things this gets right that a naive version does not:

- **Listing is unfiltered.** A server-side `content_types[]` filter drops files
  uploaded with an empty content type, which is how a surprising share of
  course PDFs arrive. List everything and match on name or type here.
- **Downloads are the one path that can destroy content.** A truncated
  `original.pdf` would later be rolled back over the good file. So the download
  goes to a `.part` file, is compared against the byte count Canvas reported,
  and is deleted on any mismatch. Pre-signed URLs are fetched without the token.
- **Uploads overwrite in place.** Same name + same folder + `on_duplicate=
  overwrite` keeps every link in the course working. Canvas issues a NEW file
  id for the replacement and chains the old one, so post-upload verification
  pairs by (display_name, folder_id), never by the old id.
"""
from __future__ import annotations

import io
import json
import os
import secrets as _secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import canvas_policy

DOC_EXT = {".pdf", ".pptx", ".docx", ".xlsx", ".ppt", ".doc", ".xls"}
DOC_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
}


def file_ext(meta: dict) -> str:
    """The extension a Canvas file record really has, by name first, type second."""
    name = (meta.get("display_name") or meta.get("filename") or "").lower()
    ext = os.path.splitext(name)[1]
    if ext in DOC_EXT:
        return ext
    return DOC_TYPES.get((meta.get("content-type") or meta.get("content_type") or "").lower(), ext)


class _ConcatReader(io.RawIOBase):
    """Stream head + a file + tail without holding the file in memory twice."""

    def __init__(self, head: bytes, path: Path, tail: bytes):
        self._parts = [io.BytesIO(head), open(path, "rb"), io.BytesIO(tail)]
        self.length = len(head) + path.stat().st_size + len(tail)
        self._i = 0

    def readable(self):
        return True

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        if n is None or n < 0:
            return b"".join(p.read() for p in self._parts[self._i:])
        out = b""
        while len(out) < n and self._i < len(self._parts):
            chunk = self._parts[self._i].read(n - len(out))
            if not chunk:
                self._i += 1
                continue
            out += chunk
        return out

    def close(self):
        for p in self._parts:
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass
        super().close()


class FilesOps:
    # ---------------------------------------------------------------- list
    def course_files(self, course_id) -> list[dict]:
        """Every file in the course. Filter on the caller's side (see file_ext)."""
        return list(self.paged(f"/courses/{course_id}/files", **{"include": ["user"]}))

    def course_documents(self, course_id, exts: set[str] | None = None) -> list[dict]:
        exts = exts or DOC_EXT
        out = []
        for f in self.course_files(course_id):
            ext = file_ext(f)
            if ext in exts:
                f["_ext"] = ext
                out.append(f)
        return out

    def course_folders(self, course_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/folders"))

    def file_info(self, file_id) -> dict:
        return self.get(f"/files/{file_id}")

    def folder_files(self, folder_id) -> list[dict]:
        return list(self.paged(f"/folders/{folder_id}/files"))

    # ------------------------------------------------------------ download
    def download_file(self, url: str, dest: Path, expect_size: int | None = None) -> Path:
        """Fetch a Canvas file URL to `dest`, atomically, verifying the byte count.

        Pre-signed URLs (S3/CloudFront) reject a bearer token, so the first try
        is unauthenticated; the token is only attached when the URL is on the
        Canvas host itself.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        attempts = [False] + ([True] if canvas_policy.same_host(self.base, url) else [])
        errors: list[str] = []
        for use_token in attempts:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", self.user_agent())
            if use_token:
                canvas_policy.assert_token_host(self.base, url, getattr(self, "allowed_hosts", None))
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(req, timeout=max(self.timeout, 300)) as resp:
                    declared = resp.headers.get("Content-Length")
                    total = 0
                    with open(part, "wb") as fh:
                        while True:
                            chunk = resp.read(1 << 18)
                            if not chunk:
                                break
                            fh.write(chunk)
                            total += len(chunk)
                want = expect_size if expect_size else (int(declared) if declared else None)
                if total == 0 or (want is not None and total != want):
                    errors.append(f"short download: got {total} bytes, expected {want}")
                    part.unlink(missing_ok=True)
                    continue
                os.replace(part, dest)
                return dest
            except urllib.error.HTTPError as exc:
                errors.append(f"HTTP {exc.code}{' with token' if use_token else ''}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                if part.exists():
                    try:
                        part.unlink()
                    except OSError:
                        pass
        raise RuntimeError(f"could not download {dest.name} ({'; '.join(errors)})")

    # -------------------------------------------------------------- upload
    # Canvas takes three steps, and they want different treatment when you are
    # uploading a whole course's worth of files:
    #
    #   1. POST /courses/:id/files asks for a slot        (token, form)
    #   2. POST the multipart to the pre-signed URL       (NO token)
    #   3. follow the Location Canvas answers with        (token) to confirm
    #
    # Step 1 is small, rate-limited, and hands back a single-use slot, so it is
    # asked for one file at a time. Step 2 is the whole file on the wire, so on
    # a slow link it is worth running several at once. `upload_course_file` does
    # all three for one file; a caller pushing hundreds of them calls
    # `request_upload_slot` in sequence and `send_upload_body` in parallel.
    def request_upload_slot(self, course_id, name: str, size: int,
                            folder_id: int | str | None = None,
                            content_type: str = "application/octet-stream",
                            on_duplicate: str = "overwrite") -> dict:
        """Step 1 on its own. The slot it returns is single-use: a failed body
        send needs a fresh one, so never retry step 2 against the same offer."""
        fields = [("name", name), ("size", str(size)), ("content_type", content_type),
                  ("on_duplicate", on_duplicate)]
        if folder_id is not None:
            fields.append(("parent_folder_id", str(folder_id)))
        return self._post_form("POST", f"/courses/{course_id}/files", fields)

    def send_upload_body(self, offer: dict, name: str, path: Path,
                         content_type: str = "application/octet-stream") -> dict:
        """Steps 2 and 3 for a slot from `request_upload_slot`, streamed from
        disk. Safe to run several of these at once."""
        return self._finish_upload(offer, name, Path(path), content_type)

    def upload_course_file(self, course_id, name: str, path: Path,
                           folder_id: int | str | None = None,
                           content_type: str = "application/octet-stream",
                           on_duplicate: str = "overwrite") -> dict:
        """All three steps for one file."""
        path = Path(path)
        offer = self.request_upload_slot(course_id, name, path.stat().st_size,
                                         folder_id, content_type, on_duplicate)
        return self.send_upload_body(offer, name, path, content_type)

    def _finish_upload(self, offer: dict, name: str, path: Path, content_type: str) -> dict:
        url = (offer or {}).get("upload_url")
        if not url:
            raise RuntimeError(f"Canvas did not offer an upload slot for {name}: {offer}")
        boundary = "----courseforge" + _secrets.token_hex(16)
        head = bytearray()
        for key, value in (offer.get("upload_params") or {}).items():
            head += f"--{boundary}\r\n".encode()
            head += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
            head += str(value).encode("utf-8") + b"\r\n"
        # A course-authored display name could otherwise end the header early.
        safe = name.replace("\r", " ").replace("\n", " ").replace('"', "%22")
        head += f"--{boundary}\r\n".encode()
        head += (f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = _ConcatReader(bytes(head), path, tail)
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(body.length))
        req.add_header("User-Agent", self.user_agent())
        req.add_header("Accept", "application/json")
        opener = urllib.request.build_opener(self._NoRedirect)
        try:
            with opener.open(req, timeout=max(self.timeout, 600)) as resp:
                status, raw, location = resp.status, resp.read(), resp.headers.get("Location")
        except urllib.error.HTTPError as exc:
            status, raw, location = exc.code, exc.read(), exc.headers.get("Location")
            if status >= 400:
                from .canvas import CanvasError
                raise CanvasError(status, url, raw.decode("utf-8", "replace")) from None
        finally:
            body.close()
        if status in (301, 302, 303, 307, 308) and location:
            payload_back, _ = self._request("GET", location)
            return payload_back or {}
        text = raw.decode("utf-8", "replace")
        return json.loads(text) if text.strip() else {}

    def find_course_file(self, course_id, display_name: str, folder_id=None) -> dict | None:
        """Pair by (display_name, folder_id): an overwrite gets a fresh id."""
        for f in self.course_files(course_id):
            if f.get("display_name") == display_name and \
                    (folder_id is None or str(f.get("folder_id")) == str(folder_id)):
                return f
        return None

    def user_agent(self) -> str:
        from .canvas import USER_AGENT
        return USER_AGENT
