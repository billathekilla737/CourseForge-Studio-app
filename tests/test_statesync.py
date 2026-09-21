"""StateSyncer: Canvas user-files replica of Studio JSON blobs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from courseforge import accommodations, statesync


class FakeRoster:
    def __init__(self, path: Path):
        self.path = path
        self._rows = []

    def load(self):
        return list(self._rows)

    def save(self, rows):
        self._rows = list(rows)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"updated": "now", "students": [s.to_json() for s in rows]}
        self.path.write_text(json.dumps(body), encoding="utf-8")


class FakeClient:
    def __init__(self):
        self.files = {}
        self.quota = {"quota": 10_000_000, "quota_used": 100}

    def user_folder_files(self, folder="canvas-grader"):
        out = []
        for (fold, name), blob in self.files.items():
            if fold == folder:
                out.append({"display_name": name, "filename": name,
                            "url": f"mem://{fold}/{name}", "id": 1})
        return out

    def read_file_bytes(self, url: str) -> bytes:
        _, _, rest = url.partition("mem://")
        folder, _, name = rest.rpartition("/")
        return self.files[(folder, name)]

    def upload_user_file(self, name, payload, folder="canvas-grader",
                         content_type="application/octet-stream"):
        self.files[(folder, name)] = payload
        return {"id": 99, "display_name": name, "folder": folder}

    def user_quota(self):
        return dict(self.quota)

    def read_user_named(self, name, folder="courseforge-studio/state"):
        return self.files.get((folder, name))

    def quota_headroom(self, need=0):
        cap = int(self.quota.get("quota") or 0)
        used = int(self.quota.get("quota_used") or 0)
        return {"quota": cap, "quota_used": used,
                "ok": True if not cap else (used + int(need) <= cap)}


def _app(tmp: Path):
    cfg = SimpleNamespace(
        data=tmp, state_to_canvas=True, allow_canvas_writes=True, state_sync_s=90,
    )
    app = SimpleNamespace(cfg=cfg, machine={"id": "aa", "name": "pc-a"},
                          roster=FakeRoster(tmp / "accommodations.json"),
                          client=FakeClient())
    app.state_sync = statesync.StateSyncer(app)
    return app


class StateSyncerTests(unittest.TestCase):
    def test_seed_then_pickup(self):
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        row = accommodations.parse_student(
            {"user_id": "11", "name": "Ada", "kind": "percent", "percent": 50})
        a.roster.save([row])
        result = a.state_sync.hydrate(statesync.ROSTER_KEY)
        self.assertIn(result["did"], ("seeded", "sent", "push"))
        self.assertIn(("courseforge-studio/state", "accommodations.json"), a.client.files)

        picked = b.state_sync.hydrate(statesync.ROSTER_KEY)
        self.assertEqual(picked["did"], "picked_up")
        loaded = b.roster.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].user_id, "11")
        self.assertEqual(loaded[0].percent, 50)

    def test_diverge_does_not_clobber(self):
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        a.roster.save([accommodations.parse_student(
            {"user_id": "11", "name": "Ada", "kind": "percent", "percent": 50})])
        a.state_sync.hydrate(statesync.ROSTER_KEY)

        b.state_sync.hydrate(statesync.ROSTER_KEY)
        b.roster.save([accommodations.parse_student(
            {"user_id": "11", "name": "Ada", "kind": "percent", "percent": 100})])
        b.state_sync.mark_dirty(statesync.ROSTER_KEY)
        b.state_sync.push(statesync.ROSTER_KEY)

        a.roster.save([accommodations.parse_student(
            {"user_id": "22", "name": "Bob", "kind": "minutes", "minutes": 20})])
        a.state_sync.mark_dirty(statesync.ROSTER_KEY)
        out = a.state_sync.hydrate(statesync.ROSTER_KEY)
        self.assertEqual(out["did"], "diverged")
        self.assertEqual([s.user_id for s in a.roster.load()], ["22"])

    def test_resolve_remote(self):
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        a.roster.save([accommodations.parse_student(
            {"user_id": "11", "name": "Ada", "kind": "percent", "percent": 50})])
        a.state_sync.hydrate(statesync.ROSTER_KEY)
        b.state_sync.hydrate(statesync.ROSTER_KEY)
        b.roster.save([accommodations.parse_student(
            {"user_id": "11", "name": "Ada", "kind": "percent", "percent": 100})])
        b.state_sync.mark_dirty(statesync.ROSTER_KEY)
        b.state_sync.push(statesync.ROSTER_KEY)
        a.roster.save([accommodations.parse_student(
            {"user_id": "22", "name": "Bob", "kind": "minutes", "minutes": 20})])
        a.state_sync.mark_dirty(statesync.ROSTER_KEY)
        a.state_sync.hydrate(statesync.ROSTER_KEY)
        a.state_sync.resolve(statesync.ROSTER_KEY, "remote")
        self.assertEqual(a.roster.load()[0].percent, 100)

    def test_put_notes(self):
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        out = a.state_sync.put("students/11.json", {"user_id": "11", "notes": "1.5x on file"})
        self.assertTrue(out.get("did") in ("sent", "in_sync") or out.get("state") in ("sent", "in_sync"))
        got = a.state_sync.get("students/11.json")
        payload = (got or {}).get("payload") or {}
        notes = payload.get("notes")
        if notes is None:
            notes = (out.get("payload") or {}).get("notes")
        self.assertEqual(notes, "1.5x on file")

    def test_assistant_chat_seeds_then_picks_up(self):
        from courseforge.assistant import sync as asst_sync
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        b.machine = {"id": "bb", "name": "pc-b"}
        a_dir = a.cfg.data / "9" / "assistant"
        a_dir.mkdir(parents=True)
        (a_dir / "conversation.txt").write_text("[You] hello from A\n", encoding="utf-8")
        (a_dir / "mode.json").write_text(json.dumps({"mode": "plan"}), encoding="utf-8")
        (a_dir / "session.json").write_text(json.dumps({
            "session_id": "11111111-1111-1111-1111-111111111111",
            "started": "now",
        }), encoding="utf-8")
        (a_dir / "settings.json").write_text(json.dumps({"hooks": "this-pc"}), encoding="utf-8")
        payload = asst_sync.pack(a_dir, "9", "aa")
        self.assertIn("hello from A", payload["files"]["conversation.txt"])
        self.assertEqual(payload["files"]["mode.json"]["mode"], "plan")
        self.assertNotIn("settings.json", payload["files"])
        same = tmp / "same-pc"
        same.mkdir()
        asst_sync.apply(same, payload, "aa")
        self.assertTrue((same / "session.json").is_file(),
                        "the same PC should keep the Claude session id")
        seeded = a.state_sync.hydrate(statesync.assistant_key("9"), payload)
        self.assertIn(seeded["did"], ("seeded", "sent", "push"))

        b_dir = b.cfg.data / "9" / "assistant"
        picked = b.state_sync.hydrate(
            statesync.assistant_key("9"),
            asst_sync.pack(b_dir, "9", "bb"),
            apply=lambda body: asst_sync.apply(b_dir, body, "bb"),
        )
        self.assertEqual(picked["did"], "picked_up")
        self.assertIn("hello from A", (b_dir / "conversation.txt").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((b_dir / "mode.json").read_text(encoding="utf-8"))["mode"], "plan")
        self.assertFalse((b_dir / "session.json").is_file(),
                         "a Claude session id from another PC must not resume here")
        self.assertFalse((b_dir / "settings.json").is_file())

    def test_assistant_skip_pull_while_session_alive(self):
        from courseforge.assistant import sync as asst_sync
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        a_dir = a.cfg.data / "9" / "assistant"
        a_dir.mkdir(parents=True)
        (a_dir / "conversation.txt").write_text("[You] from A\n", encoding="utf-8")
        a.state_sync.hydrate(statesync.assistant_key("9"), asst_sync.pack(a_dir, "9", "aa"))

        b_dir = b.cfg.data / "9" / "assistant"
        b_dir.mkdir(parents=True)
        (b_dir / "conversation.txt").write_text("[You] live on B\n", encoding="utf-8")
        out = b.state_sync.hydrate(
            statesync.assistant_key("9"),
            asst_sync.pack(b_dir, "9", "aa"),
            apply=lambda body: asst_sync.apply(b_dir, body, "aa"),
            skip_pull=True,
        )
        self.assertEqual(out["did"], "skipped_pull")
        self.assertIn("live on B", (b_dir / "conversation.txt").read_text(encoding="utf-8"))

    def test_build_drafts_seed_then_pick_up(self):
        from courseforge.content.paths import BuildDir
        from courseforge.content import sync as build_sync
        tmp = Path(tempfile.mkdtemp())
        a = _app(tmp / "a")
        b = _app(tmp / "b")
        b.client = a.client
        build_a = BuildDir(a.cfg.data / "9", "9")
        build_a.save_draft({
            "id": "d1", "title": "Week 1", "kind": "page",
            "html": "<p>hello</p>", "placed": None,
        })
        (build_a.backups_dir / "syllabus.html").parent.mkdir(parents=True, exist_ok=True)
        (build_a.backups_dir / "syllabus.html").write_text("<p>old</p>", encoding="utf-8")
        payload = build_sync.pack(build_a, "aa")
        self.assertIn("d1", payload["files"]["drafts"])
        self.assertNotIn("backups", payload["files"])
        a.state_sync.hydrate(statesync.build_key("9"), payload)

        build_b = BuildDir(b.cfg.data / "9", "9")
        picked = b.state_sync.hydrate(
            statesync.build_key("9"),
            build_sync.pack(build_b, "bb"),
            apply=lambda body: build_sync.apply(build_b, body),
        )
        self.assertEqual(picked["did"], "picked_up")
        got = build_b.draft("d1")
        self.assertIsNotNone(got)
        self.assertEqual(got["title"], "Week 1")
        self.assertFalse((build_b.backups_dir / "syllabus.html").is_file())


if __name__ == "__main__":
    unittest.main()
