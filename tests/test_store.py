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

    def test_create_imported_session_stores_private_transcript_and_safe_provenance(self) -> None:
        transcript = "User: Please continue the migration.\nAssistant: I updated the tests.\n"

        session = self.store.create_imported_session(
            project_path=self.project_dir,
            title="Migration discussion",
            transcript=transcript,
            source_name="/home/alice/Downloads/export.md",
            source_format="markdown",
            now=self.now,
            session_id="import-one",
        )

        self.assertEqual("external", session.agent)
        self.assertEqual("completed", session.status)
        self.assertEqual("export.md", session.source_name)
        self.assertEqual("markdown", session.source_format)
        self.assertEqual(transcript, self.store.get_imported_conversation(session.id))
        session_dir = (
            self.data_dir / "projects" / session.project_id / "sessions" / session.id
        )
        self.assertEqual(0o600, (session_dir / "import.md").stat().st_mode & 0o777)
        self.assertNotIn("/home/alice", (session_dir / "meta.json").read_text())
        self.assertIn("Imported conversation", (session_dir / "note.md").read_text())

    def test_imported_transcript_validation_is_bounded_and_metadata_is_safe(self) -> None:
        common = {
            "project_path": self.project_dir,
            "title": "Imported chat",
            "source_name": "chat.md",
            "source_format": "markdown",
            "now": self.now,
        }
        with self.assertRaisesRegex(InvalidSessionError, "transcript must not be empty"):
            self.store.create_imported_session(transcript=" \n", **common)
        with self.assertRaisesRegex(InvalidSessionError, "2 MiB"):
            self.store.create_imported_session(transcript="x" * (2 * 1024 * 1024 + 1), **common)
        with self.assertRaisesRegex(InvalidSessionError, "source_name"):
            self.store.create_imported_session(
                transcript="hello",
                source_name="bad\nname.md",
                **{key: value for key, value in common.items() if key != "source_name"},
            )
        with self.assertRaisesRegex(InvalidSessionError, "source_format"):
            self.store.create_imported_session(
                transcript="hello",
                source_format="md\nunsafe",
                **{key: value for key, value in common.items() if key != "source_format"},
            )
        for unsafe_title in ("Title\n## Warnings", "x" * 201, "Title\u202e"):
            with self.subTest(title=unsafe_title), self.assertRaisesRegex(
                InvalidSessionError, "title"
            ):
                self.store.create_imported_session(
                    transcript="hello", title=unsafe_title,
                    **{key: value for key, value in common.items() if key != "title"},
                )

    def test_imported_conversation_can_be_bounded_and_delete_allows_import_file(self) -> None:
        session = self.store.create_imported_session(
            project_path=self.project_dir,
            title="Imported chat",
            transcript="abcdefghij",
            source_name="chat.txt",
            source_format="text",
            now=self.now,
            session_id="import-two",
        )

        self.assertEqual(
            "abcd", self.store.get_imported_conversation(session.id, max_chars=4)
        )
        self.store.delete_session(session.id)

        self.assertEqual([], self.store.list_sessions())

    def test_regular_session_has_no_imported_conversation(self) -> None:
        session = self.create_session()

        self.assertIsNone(self.store.get_imported_conversation(session.id))

    def test_failed_import_does_not_leave_a_partial_session(self) -> None:
        self.store.initialize()
        real_write = self.store._atomic_write

        def fail_note(path: Path, content: str) -> None:
            if path.name == "note.md":
                raise OSError("simulated write failure")
            real_write(path, content)

        with patch.object(self.store, "_atomic_write", side_effect=fail_note):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.store.create_imported_session(
                    project_path=self.project_dir,
                    title="Imported chat",
                    transcript="conversation",
                    source_name="chat.txt",
                    source_format="text",
                    now=self.now,
                    session_id="failed-import",
                )

        self.assertEqual([], self.store.list_sessions())
        leftovers = list((self.data_dir / "projects").rglob("*failed-import*"))
        self.assertEqual([], leftovers)

    def test_missing_imported_transcript_is_reported_as_corrupt(self) -> None:
        session = self.store.create_imported_session(
            project_path=self.project_dir,
            title="Imported chat",
            transcript="conversation",
            source_name="chat.txt",
            source_format="text",
            now=self.now,
            session_id="missing-import",
        )
        session_dir = (
            self.data_dir / "projects" / session.project_id / "sessions" / session.id
        )
        (session_dir / "import.md").unlink()

        with self.assertRaisesRegex(StoreCorruptError, "Imported conversation is missing"):
            self.store.get_imported_conversation(session.id)

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

    def test_mark_interrupted_if_active_only_closes_active_sessions(self) -> None:
        active = self.create_session()
        completed = self.create_session(
            session_id="20260829T104012Z-c3d4",
            now=self.now + timedelta(minutes=10),
        )
        self.store.add_checkpoint(completed.id, status="completed")
        later = self.now + timedelta(minutes=20)

        closed = self.store.mark_interrupted_if_active(active.id, now=later)
        untouched = self.store.mark_interrupted_if_active(completed.id, now=later)

        self.assertEqual("interrupted", closed.status)
        self.assertEqual("completed", untouched.status)

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

    def test_symlink_ancestors_block_reads_writes_and_deletes(self) -> None:
        session = self.create_session()
        session_dir = self.data_dir / "projects" / session.project_id / "sessions" / session.id
        # Exercise each level, including a symlink above the store root.
        for directory in (session_dir, session_dir.parent, session_dir.parent.parent,
                          self.data_dir / "projects", self.data_dir):
            with self.subTest(directory=directory):
                external = Path(self.temp_dir) / "external"
                directory.rename(external)
                directory.symlink_to(external, target_is_directory=True)
                before = {str(p.relative_to(external)): p.read_bytes()
                          for p in external.rglob("*") if p.is_file()}
                try:
                    for action in (
                        lambda: self.store.get_session(session.id),
                        lambda: self.store.add_checkpoint(session.id, completed=["unsafe"]),
                        lambda: self.store.save_context_packet(session.id, "unsafe"),
                        lambda: self.store.delete_session(session.id),
                        self.store.reindex,
                    ):
                        with self.assertRaises(InvalidDataPathError):
                            action()
                    after = {str(p.relative_to(external)): p.read_bytes()
                             for p in external.rglob("*") if p.is_file()}
                    self.assertEqual(before, after)
                finally:
                    directory.unlink()
                    external.rename(directory)

    def test_initialize_rejects_symlink_above_missing_root_without_creating_files(self) -> None:
        external = Path(self.temp_dir) / "external"
        external.mkdir()
        linked = Path(self.temp_dir) / "linked"
        linked.symlink_to(external, target_is_directory=True)
        with self.assertRaises(InvalidDataPathError):
            SessionStore(linked / "new-data").initialize()
        self.assertEqual([], list(external.iterdir()))

    def test_multiline_checkpoint_survives_rewrite_and_resolution(self) -> None:
        goal = "## Goal inside content\n\n- nested item\n  indentation"
        session = self.create_session(goal=goal)
        items = ["Same first line\n## Unknown heading\n\nline two",
                 "Same first line\n## Pending\n- nested item\n  indentation"]
        self.store.add_checkpoint(session.id, pending=items)
        self.store.set_pinned(session.id, True)
        _, sections = self.store.get_session_sections(session.id)
        self.assertEqual([goal], sections["Goal"])
        self.assertEqual(items, sections["Pending"])
        self.store.add_checkpoint(session.id, resolve_pending=[items[0]])
        _, sections = self.store.get_session_sections(session.id)
        self.assertEqual([items[1]], sections["Pending"])
        self.assertEqual([items[0]], sections["Completed"])

    def test_legacy_note_can_be_read_and_upgraded(self) -> None:
        session = self.create_session()
        path = self.data_dir / "projects" / session.project_id / "sessions" / session.id / "note.md"
        path.write_text("---\nid: legacy\n---\n\n## Goal\nOld goal\n\n"
                        "## Pending\n- Old task\n\n## Completed\n- Old work\n")
        self.store.add_checkpoint(session.id, pending=["New task\n## Heading\nMore"])
        _, sections = self.store.get_session_sections(session.id)
        self.assertEqual(["Old goal"], sections["Goal"])
        self.assertEqual(["Old task", "New task\n## Heading\nMore"], sections["Pending"])
        self.assertEqual(["Old work"], sections["Completed"])

    def test_windows_line_endings_preserve_multiline_items(self) -> None:
        session = self.create_session()
        item = "First line\n## Nested heading\n\nLast line"
        self.store.add_checkpoint(session.id, pending=[item])
        path = (self.data_dir / "projects" / session.project_id
                / "sessions" / session.id / "note.md")
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.store.set_pinned(session.id, True)
        _, sections = self.store.get_session_sections(session.id)
        self.assertEqual([item], sections["Pending"])

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
