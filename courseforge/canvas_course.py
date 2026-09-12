"""Whole-course operations for CanvasClient: export to .imscc, import a cartridge,
copy one course into another, create a shell, and count what a course holds.

Mixed into CanvasClient (see canvas.py). Exports and migrations are asynchronous
on Canvas's side, so each has a `wait_*` that polls and reports progress.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

Progress = Callable[[str, int | None, int | None], None] | None


class CourseOps:
    # ------------------------------------------------------------ counts
    def course_counts(self, course_id) -> dict:
        """How populated a course is. Used by the import gate: a populated
        destination is refused unless the caller says otherwise."""
        counts = {}
        for name, path in (("pages", "pages"), ("modules", "modules"),
                           ("assignments", "assignments"), ("quizzes", "quizzes"),
                           ("discussions", "discussion_topics"), ("files", "files")):
            try:
                counts[name] = sum(1 for _ in self.paged(f"/courses/{course_id}/{path}"))
            except Exception as exc:  # noqa: BLE001
                counts[name] = f"error: {exc}"
        return counts

    # ------------------------------------------------------------ export
    def start_export(self, course_id, export_type: str = "common_cartridge") -> dict:
        return self._post_form("POST", f"/courses/{course_id}/content_exports",
                               [("export_type", export_type), ("skip_notifications", "true")])

    def export_status(self, course_id, export_id) -> dict:
        return self.get(f"/courses/{course_id}/content_exports/{export_id}")

    def wait_export(self, course_id, export_id, timeout_s: int = 1800,
                    progress: Progress = None) -> dict:
        """Poll until exported or failed. `waiting_for_external_tool` is simply
        another state Canvas passes through; keep polling."""
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            info = self.export_status(course_id, export_id) or {}
            state = info.get("workflow_state", "")
            pct = info.get("progress_url") and None
            if state != last and progress:
                progress(f"export {state}", None, None)
                last = state
            if state in ("exported", "failed"):
                return info
            time.sleep(5)
        raise TimeoutError("the export did not finish in time")

    def download_export(self, info: dict, dest: Path) -> Path:
        url = ((info or {}).get("attachment") or {}).get("url")
        if not url:
            raise RuntimeError("the export finished without a downloadable file")
        path = self.download_file(url, dest)
        if path.stat().st_size < 1024:
            path.unlink(missing_ok=True)
            raise RuntimeError("the export file is under 1 KB, which is not a real cartridge")
        return path

    # ---------------------------------------------------------- migration
    def start_course_copy(self, dest_course_id, source_course_id) -> dict:
        return self._post_form("POST", f"/courses/{dest_course_id}/content_migrations", [
            ("migration_type", "course_copy_importer"),
            ("settings[source_course_id]", str(source_course_id)),
        ])

    def start_cartridge_import(self, dest_course_id, cartridge: Path) -> dict:
        """Three-step: request the migration with a pre_attachment, upload the
        file to the slot Canvas returns, then read the migration back."""
        cartridge = Path(cartridge)
        offer = self._post_form("POST", f"/courses/{dest_course_id}/content_migrations", [
            ("migration_type", "common_cartridge_importer"),
            ("pre_attachment[name]", cartridge.name),
            ("pre_attachment[size]", str(cartridge.stat().st_size)),
        ])
        pre = (offer or {}).get("pre_attachment") or {}
        if not pre.get("upload_url"):
            raise RuntimeError(f"Canvas did not offer an upload slot for the cartridge: {offer}")
        self._finish_upload(pre, cartridge.name, cartridge, "application/zip")
        return self.migration_status(dest_course_id, offer["id"])

    def migration_status(self, course_id, migration_id) -> dict:
        return self.get(f"/courses/{course_id}/content_migrations/{migration_id}")

    def migration_issues(self, course_id, migration_id) -> list[dict]:
        return list(self.paged(f"/courses/{course_id}/content_migrations/{migration_id}/migration_issues"))

    def wait_migration(self, course_id, migration_id, timeout_s: int = 3600,
                       progress: Progress = None) -> dict:
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            info = self.migration_status(course_id, migration_id) or {}
            state = info.get("workflow_state", "")
            if progress:
                pct = None
                purl = info.get("progress_url")
                if purl:
                    try:
                        prog = self.get(purl) or {}
                        pct = prog.get("completion")
                    except Exception:  # noqa: BLE001
                        pct = None
                if state != last or pct is not None:
                    progress(f"import {state}" + (f" ({pct:.0f}%)" if pct is not None else ""),
                             int(pct) if pct is not None else None, 100 if pct is not None else None)
                    last = state
            if state in ("completed", "failed"):
                return info
            time.sleep(5)
        raise TimeoutError("the import did not finish in time")

    # --------------------------------------------------------------- shells
    def accounts(self) -> list[dict]:
        try:
            return list(self.paged("/accounts"))
        except Exception:  # noqa: BLE001
            return []

    def create_course(self, account_id, name: str, course_code: str = "") -> dict:
        fields = [("course[name]", name)]
        if course_code:
            fields.append(("course[course_code]", course_code))
        return self._post_form("POST", f"/accounts/{account_id}/courses", fields)
