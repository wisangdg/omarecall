from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase

from omarecall.cli import main


class CliTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_dir = Path(self.temp_dir) / "data"
        self.project_dir = Path(self.temp_dir) / "project"
        self.project_dir.mkdir()

    def run_cli(self, *args: str) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["--data-dir", str(self.data_dir), *args])
        output = stdout.getvalue() if exit_code == 0 else stderr.getvalue()
        return exit_code, json.loads(output)

    def test_create_list_show_checkpoint_archive_and_reindex(self) -> None:
        create_code, created = self.run_cli(
            "session",
            "create",
            "--project",
            str(self.project_dir),
            "--agent",
            "codex",
            "--goal",
            "Build CLI",
        )
        session_id = str(created["session"]["id"])

        checkpoint_code, checkpointed = self.run_cli(
            "checkpoint",
            session_id,
            "--completed",
            "Created CLI",
            "--pending",
            "Build UI",
        )
        list_code, listed = self.run_cli("session", "list")
        show_code, shown = self.run_cli("session", "show", session_id)
        archive_code, archived = self.run_cli("archive", session_id)
        reindex_code, reindexed = self.run_cli("reindex")

        self.assertEqual([0, 0, 0, 0, 0, 0], [
            create_code,
            checkpoint_code,
            list_code,
            show_code,
            archive_code,
            reindex_code,
        ])
        self.assertEqual(session_id, checkpointed["session"]["id"])
        self.assertEqual(1, len(listed["sessions"]))
        self.assertIn("- Created CLI", shown["session"]["note"])
        self.assertEqual("archived", archived["session"]["status"])
        self.assertEqual(1, reindexed["session_count"])

    def test_error_is_machine_readable_and_nonzero(self) -> None:
        exit_code, output = self.run_cli("session", "show", "missing")

        self.assertEqual(2, exit_code)
        self.assertEqual("session_not_found", output["error"]["code"])

    def test_context_build_returns_preview_payload(self) -> None:
        _, created = self.run_cli(
            "session",
            "create",
            "--project",
            str(self.project_dir),
            "--agent",
            "codex",
            "--goal",
            "Build preview",
        )
        session_id = str(created["session"]["id"])

        exit_code, output = self.run_cli(
            "context",
            "build",
            "--mode",
            "session",
            "--session-id",
            session_id,
            "--max-tokens",
            "1000",
        )

        self.assertEqual(0, exit_code)
        self.assertIn("OMARECALL MEMORY", output["context"]["packet"])
        self.assertEqual([session_id], output["context"]["source_session_ids"])

    def test_launch_dry_run_returns_safe_command_plan(self) -> None:
        _, created = self.run_cli(
            "session",
            "create",
            "--project",
            str(self.project_dir),
            "--agent",
            "codex",
            "--goal",
            "Source work",
        )

        exit_code, output = self.run_cli(
            "launch",
            "--dry-run",
            "--agent",
            "codex",
            "--project",
            str(self.project_dir),
            "--goal",
            "Continue work",
            "--mode",
            "session",
            "--session-id",
            str(created["session"]["id"]),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual("codex", output["launch"]["agent"])
        self.assertNotIn("OMARECALL MEMORY", " ".join(output["launch"]["agent_argv"]))

    def test_pin_and_confirmed_delete_are_exposed_as_json(self) -> None:
        _, created = self.run_cli(
            "session",
            "create",
            "--project",
            str(self.project_dir),
            "--agent",
            "codex",
            "--goal",
            "Temporary session",
        )
        session_id = str(created["session"]["id"])

        pin_code, pinned = self.run_cli("pin", session_id, "--value", "true")
        delete_code, deleted = self.run_cli(
            "delete", session_id, "--confirm", session_id
        )

        self.assertEqual(0, pin_code)
        self.assertTrue(pinned["session"]["pinned"])
        self.assertEqual(0, delete_code)
        self.assertEqual(session_id, deleted["deleted_session_id"])

    def test_import_conversation_creates_completed_recallable_session(self) -> None:
        source = Path(self.temp_dir) / "outside-conversation.md"
        source.write_text("**User:** Continue the dashboard\n\n**Assistant:** Add tests first\n")

        import_code, imported = self.run_cli(
            "import",
            "--file",
            str(source),
            "--project",
            str(self.project_dir),
        )
        session_id = str(imported["session"]["id"])
        show_code, shown = self.run_cli(
            "session", "show", session_id, "--max-import-chars", "20"
        )
        context_code, context = self.run_cli(
            "context",
            "build",
            "--mode",
            "session",
            "--session-id",
            session_id,
            "--max-tokens",
            "1000",
        )

        self.assertEqual(0, import_code)
        self.assertEqual("completed", imported["session"]["status"])
        self.assertEqual("outside-conversation.md", imported["import"]["source_name"])
        self.assertEqual("markdown", imported["import"]["format"])
        self.assertEqual(0, show_code)
        self.assertTrue(str(shown["session"]["imported_conversation"]).endswith(
            "[IMPORT TRUNCATED]"
        ))
        self.assertEqual(0, context_code)
        self.assertIn("Continue the dashboard", context["context"]["packet"])

    def test_import_error_is_machine_readable(self) -> None:
        source = Path(self.temp_dir) / "broken.json"
        source.write_text("{not json")

        exit_code, output = self.run_cli(
            "import",
            "--file",
            str(source),
            "--project",
            str(self.project_dir),
        )

        self.assertEqual(2, exit_code)
        self.assertEqual("invalid_import", output["error"]["code"])
