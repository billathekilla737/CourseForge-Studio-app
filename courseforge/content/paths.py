"""Where the Build area keeps its work for one course: data/<cid>/build/.

    state.json        what this tool created in the course (pages by key -> slug,
                      page_id, title; module ids; item ids; assignment,
                      discussion and quiz ids by key). Its presence with the
                      right course_id is what proves this tool built the
                      modules, which is the module-wipe safety gate.
    manifest.json     the saved manifest (pages-only or project)
    rubrics.json      the saved rubric definitions
    drafts/<id>.json  Claude drafts and the human edits on top of them
    last_push.json    the last push result, for the hub card
    backups/          the syllabus body before it was replaced

Drafts, state, manifest and rubrics also copy to Canvas user files
(courseforge-studio/state/build-<cid>.json) so another PC can pick them up.
backups/ and last_push.json stay on this computer. Nothing here is student data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


# Set by content.routes.install so a draft save also copies to Canvas user files.
on_change = None


def notify(course_id) -> None:
    fn = on_change
    if callable(fn):
        try:
            fn(str(course_id))
        except Exception:  # noqa: BLE001
            pass


def build_dir(course_dir: Path) -> Path:
    path = Path(course_dir) / "build"
    path.mkdir(parents=True, exist_ok=True)
    return path


class BuildDir:
    def __init__(self, course_dir: Path, course_id):
        self.course_id = str(course_id)
        self.root = build_dir(course_dir)
        self.state_path = self.root / "state.json"
        self.manifest_path = self.root / "manifest.json"
        self.rubrics_path = self.root / "rubrics.json"
        self.last_push_path = self.root / "last_push.json"
        self.drafts_dir = self.root / "drafts"
        self.backups_dir = self.root / "backups"

    # ------------------------------------------------------------------ io
    @staticmethod
    def read_json(path: Path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def write_json(path: Path, payload) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    # -------------------------------------------------------------- drafts
    def draft_path(self, draft_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", str(draft_id))[:40]
        if not safe:
            raise ValueError("draft id is empty")
        return self.drafts_dir / f"{safe}.json"

    def drafts(self) -> list[dict]:
        rows = []
        if self.drafts_dir.is_dir():
            for path in sorted(self.drafts_dir.glob("*.json")):
                data = self.read_json(path)
                if isinstance(data, dict) and data.get("id"):
                    rows.append(data)
        rows.sort(key=lambda d: d.get("updated_at") or d.get("created_at") or "", reverse=True)
        return rows

    def draft(self, draft_id: str) -> dict | None:
        return self.read_json(self.draft_path(draft_id))

    def save_draft(self, draft: dict) -> Path:
        path = self.write_json(self.draft_path(draft["id"]), draft)
        notify(self.course_id)
        return path

    def delete_draft(self, draft_id: str) -> bool:
        path = self.draft_path(draft_id)
        if path.is_file():
            path.unlink()
            notify(self.course_id)
            return True
        return False

    # ------------------------------------------------------------ manifest
    def manifest(self) -> dict | None:
        return self.read_json(self.manifest_path)

    def save_manifest(self, manifest: dict) -> Path:
        path = self.write_json(self.manifest_path, manifest)
        notify(self.course_id)
        return path

    def rubrics(self) -> list | None:
        return self.read_json(self.rubrics_path)

    def save_rubrics(self, entries) -> Path:
        path = self.write_json(self.rubrics_path, entries)
        notify(self.course_id)
        return path

    def last_push(self) -> dict | None:
        return self.read_json(self.last_push_path)

    def save_last_push(self, result: dict) -> Path:
        return self.write_json(self.last_push_path, result)
