from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from omarecall.errors import (
    InvalidDataPathError,
    InvalidSessionError,
    SessionExistsError,
    SessionNotFoundError,
    StoreCorruptError,
)
from omarecall.store import SessionStore


class SessionStoreTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.data_dir = Path(self.temp_dir) / "data"
        self.project_dir = Path(self.temp_dir) / "My Project"
        self.project_dir.mkdir()
        self.store = SessionStore(self.data_dir)
        self.now = datetime(2026, 8, 29, 10, 30, 12, tzinfo=UTC)

    def create_session(self, **overrides: object):
        values: dict[str, object] = {
            "project_path": self.project_dir,
            "agent": "codex",
            "goal": "Build the session store",
            "title": "Storage foundation",
            "now": self.now,
            "session_id": "20260829T103012Z-a1b2",
        }
        values.update(overrides)
        return self.store.create_session(**values)

    def test_initialize_creates_private_rebuildable_indexes(self) -> None:
        self.store.initialize()

        self.assertEqual(0o700, self.data_dir.stat().st_mode & 0o777)
        for name in ("projects.json", "index.json"):
            path = self.data_dir / name
            self.assertTrue(path.is_file())
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(1, json.loads(path.read_text())["schema_version"])

    def test_create_session_writes_metadata_note_and_project(self) -> None:
        session = self.create_session()

        self.assertEqual("20260829T103012Z-a1b2", session.id)
        self.assertEqual("my-project", session.project_name)
        self.assertEqual("active", session.status)
        self.assertEqual(str(self.project_dir.resolve()), session.project_path)

        session_dir = self.data_dir / "projects" / session.project_id / "sessions" / session.id
        metadata = json.loads((session_dir / "meta.json").read_text())
        note = (session_dir / "note.md").read_text()

        self.assertEqual(session.id, metadata["id"])
        self.assertIn("## Goal\nBuild the session store", note)
        self.assertIn("## Completed\n", note)
        self.assertEqual(0o600, (session_dir / "meta.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, (session_dir / "note.md").stat().st_mode & 0o777)

        projects = json.loads((self.data_dir / "projects.json").read_text())
        self.assertIn(session.project_id, projects["projects"])

    def test_project_ids_do_not_collide_for_equal_directory_names(self) -> None:
        other = Path(self.temp_dir) / "nested" / "My Project"
        other.mkdir(parents=True)

        first = self.create_session()
        second = self.create_session(
            project_path=other,
            session_id="20260829T103112Z-c3d4",
            now=self.now + timedelta(minutes=1),
        )

        self.assertNotEqual(first.project_id, second.project_id)

    def test_explicit_session_id_must_be_globally_unique(self) -> None:
        other = Path(self.temp_dir) / "other"
        other.mkdir()
        self.create_session()

        with self.assertRaisesRegex(SessionExistsError, "already exists"):
            self.create_session(project_path=other)

    def test_checkpoint_merges_unique_items_and_updates_status(self) -> None:
        session = self.create_session()
        later = self.now + timedelta(minutes=15)

        updated = self.store.add_checkpoint(
            session.id,
            completed=["Created manifest", "Created manifest"],
            decisions=["Use standard library only"],
            pending=["Add context builder"],
            files=["manifest.json"],
            warnings=["QML not validated"],
            status="completed",
            now=later,
        )

        self.assertEqual("completed", updated.status)
        self.assertEqual(later, updated.updated_at)
        note = self.store.get_session(session.id)["note"]
        self.assertEqual(1, note.count("- Created manifest"))
        self.assertIn("- Use standard library only", note)
        self.assertIn("- Add context builder", note)
        self.assertIn("- manifest.json", note)
        self.assertIn("- QML not validated", note)

    def test_pin_changes_metadata_and_relevant_priority(self) -> None:
        first = self.create_session()
        second = self.create_session(
            session_id="20260829T104012Z-c3d4",
            now=self.now + timedelta(minutes=10),
        )

        pinned = self.store.set_pinned(first.id, True, now=self.now + timedelta(minutes=20))

        self.assertTrue(pinned.pinned)
        self.assertFalse(self.store.get_session_metadata(second.id).pinned)

    def test_delete_removes_owned_session_and_index_entry(self) -> None:
        session = self.create_session()
        self.store.save_context_packet(session.id, "redacted context")

        self.store.delete_session(session.id)

        self.assertEqual([], self.store.list_sessions())
        with self.assertRaises(SessionNotFoundError):
            self.store.get_session(session.id)

    def test_list_sessions_is_newest_first_and_filterable(self) -> None:
        first = self.create_session()
        second = self.create_session(
            session_id="20260829T104012Z-c3d4",
            now=self.now + timedelta(minutes=10),
            agent="claude",
        )
        self.store.archive_session(first.id, now=self.now + timedelta(minutes=20))

        all_sessions = self.store.list_sessions()
        active_sessions = self.store.list_sessions(status="active")
        project_sessions = self.store.list_sessions(project_id=first.project_id)

        self.assertEqual([first.id, second.id], [item.id for item in all_sessions])
        self.assertEqual([second.id], [item.id for item in active_sessions])
        self.assertEqual(2, len(project_sessions))
        self.assertEqual(1, len(self.store.list_sessions(limit=1)))

    def test_reindex_recovers_index_from_session_metadata(self) -> None:
        session = self.create_session()
        (self.data_dir / "index.json").unlink()

        count = self.store.reindex()

        self.assertEqual(1, count)
        self.assertEqual(session.id, self.store.list_sessions()[0].id)

    def test_missing_session_has_specific_error(self) -> None:
        with self.assertRaisesRegex(SessionNotFoundError, "missing"):
            self.store.get_session("missing")

    def test_symlink_data_directory_is_rejected(self) -> None:
        target = Path(self.temp_dir) / "target"
        target.mkdir()
        symlink = Path(self.temp_dir) / "linked-data"
        symlink.symlink_to(target, target_is_directory=True)

        with self.assertRaises(InvalidDataPathError):
            SessionStore(symlink).initialize()

    def test_nested_symlink_is_rejected_before_writing_outside_store(self) -> None:
        self.store.initialize()
        project_id = (
            "my-project-"
            + hashlib.sha256(str(self.project_dir.resolve()).encode()).hexdigest()[:10]
        )
        external = Path(self.temp_dir) / "external"
        external.mkdir()
        (self.data_dir / "projects" / project_id).symlink_to(
            external, target_is_directory=True
        )

        with self.assertRaises(InvalidDataPathError):
            self.create_session()

        self.assertFalse((external / "sessions").exists())

    def test_path_traversal_session_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidSessionError, "Invalid session ID"):
            self.store.get_session("../outside")

    def test_non_regular_note_is_rejected_without_reading_it(self) -> None:
        session = self.create_session()
        session_dir = (
            self.data_dir / "projects" / session.project_id / "sessions" / session.id
        )
        note = session_dir / "note.md"
        note.unlink()
        note.mkdir()

        with self.assertRaises(InvalidDataPathError):
            self.store.get_session(session.id)

    def test_traversal_in_stored_metadata_is_rejected(self) -> None:
        session = self.create_session()
        session_dir = (
            self.data_dir / "projects" / session.project_id / "sessions" / session.id
        )
        metadata_path = session_dir / "meta.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["project_id"] = "../outside"
        metadata_path.write_text(json.dumps(metadata))

        with self.assertRaises(StoreCorruptError):
            self.store.reindex()

    def test_atomic_write_does_not_leave_temp_files(self) -> None:
        self.create_session()

        leftovers = [path for path in self.data_dir.rglob("*") if ".tmp-" in path.name]
        self.assertEqual([], leftovers)

    def test_default_root_uses_xdg_data_home(self) -> None:
        expected = Path(self.temp_dir) / "xdg" / "omarecall"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(Path(self.temp_dir) / "xdg")}):
            store = SessionStore.default()

        self.assertEqual(expected, store.root)
