from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

from omarecall.context_builder import ContextBuilder
from omarecall.errors import InvalidContextRequestError, UnsupportedAgentError
from omarecall.launcher import AgentLauncher, LaunchRequest
from omarecall.store import SessionStore


class AgentLauncherTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(self.temp_dir)
        self.project = root / "project with spaces"
        self.project.mkdir()
        self.store = SessionStore(root / "data")
        self.source = self.store.create_session(
            project_path=self.project,
            agent="codex",
            goal="Original work",
            session_id="source-session",
            now=datetime(2026, 8, 29, 13, 0, tzinfo=UTC),
        )
        self.store.add_checkpoint(
            self.source.id,
            decisions=["API_KEY=do-not-leak"],
        )
        self.executable = lambda name: f"/mock/bin/{name}"
        self.launcher = AgentLauncher(
            self.store,
            executable_resolver=self.executable,
            now=lambda: datetime(2026, 8, 29, 14, 0, tzinfo=UTC),
        )

    def request(self, **overrides: object) -> LaunchRequest:
        values: dict[str, object] = {
            "project_path": self.project,
            "agent": "codex",
            "goal": "Continue the work",
            "mode": "session",
            "source_session_id": self.source.id,
            "max_tokens": 1_000,
        }
        values.update(overrides)
        return LaunchRequest(**values)

    def test_prepare_keeps_memory_out_of_process_arguments(self) -> None:
        plan = self.launcher.prepare(self.request())

        self.assertNotEqual(self.source.id, plan.session.id)
        self.assertIsNotNone(plan.context_path)
        context_path = Path(str(plan.context_path))
        context = context_path.read_text()
        argv_text = " ".join(plan.agent_argv)

        self.assertIn("API_KEY=[REDACTED]", context)
        self.assertNotIn("do-not-leak", context)
        self.assertNotIn("OMARECALL MEMORY", argv_text)
        self.assertNotIn("do-not-leak", argv_text)
        self.assertIn(str(context_path), argv_text)
        self.assertIn(str(self.store.root), plan.agent_argv)
        self.assertEqual(0o600, context_path.stat().st_mode & 0o777)
        self.assertEqual(str(self.project.resolve()), plan.cwd)

    def test_clean_mode_creates_session_without_context_file(self) -> None:
        plan = self.launcher.prepare(
            self.request(mode="clean", source_session_id=None)
        )

        self.assertIsNone(plan.context_path)
        self.assertIn("checkpoint", " ".join(plan.agent_argv))

    def test_supported_adapters_build_interactive_commands(self) -> None:
        cases = {
            "claude": [
                "/mock/bin/claude",
                "--permission-mode",
                "auto",
                "--add-dir",
                str(self.store.root),
                "--",
            ],
            "codex": [
                "/mock/bin/codex",
                "--approve-for-me",
                "-C",
                str(self.project.resolve()),
                "--add-dir",
                str(self.store.root),
                "--",
            ],
            "copilot": ["/mock/bin/copilot", "--allow-all", "--interactive"],
            "crush": ["/mock/bin/crush", "run"],
            "grok": [
                "/mock/bin/grok",
                "--permission-mode",
                "bypassPermissions",
                "--",
            ],
            "omp": ["/mock/bin/omp", "--auto-approve", "--"],
            "opencode": ["/mock/bin/opencode", "--auto", "--prompt"],
            "pi": ["/mock/bin/pi"],
            "agy": [
                "/mock/bin/agy",
                "--dangerously-skip-permissions",
                "--add-dir",
                str(self.store.root),
                "-i",
            ],
        }
        for agent, prefix in cases.items():
            with self.subTest(agent=agent):
                plan = self.launcher.prepare(
                    self.request(agent=agent, mode="clean", source_session_id=None)
                )
                self.assertEqual(tuple(prefix), plan.agent_argv[:-1])
                self.assertIn("OmaRecall session:", plan.agent_argv[-1])
                self.assertIn("checkpoint", plan.agent_argv[-1])

    def test_missing_agent_binary_is_rejected_before_session_creation(self) -> None:
        launcher = AgentLauncher(
            self.store,
            executable_resolver=lambda _: None,
        )
        count_before = len(self.store.list_sessions())

        with self.assertRaises(UnsupportedAgentError):
            launcher.prepare(self.request(agent="codex"))

        self.assertEqual(count_before, len(self.store.list_sessions()))

    def test_unknown_explicit_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(UnsupportedAgentError, "unknown-agent"):
            self.launcher.prepare(self.request(agent="unknown-agent"))

    def test_deprecated_gemini_explicit_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(UnsupportedAgentError, "gemini"):
            self.launcher.prepare(self.request(agent="gemini"))

    def test_deprecated_gemini_default_is_rejected_before_session_creation(self) -> None:
        launcher = AgentLauncher(
            self.store,
            executable_resolver=self.executable,
            default_agent_resolver=lambda: "gemini",
        )
        count_before = len(self.store.list_sessions())

        with self.assertRaisesRegex(UnsupportedAgentError, "gemini"):
            launcher.prepare(
                self.request(agent=None, mode="clean", source_session_id=None)
            )

        self.assertEqual(count_before, len(self.store.list_sessions()))

    def test_unknown_default_agent_uses_omarchy_launcher_fallback(self) -> None:
        launcher = AgentLauncher(
            self.store,
            executable_resolver=self.executable,
            default_agent_resolver=lambda: "future-agent",
        )

        plan = launcher.prepare(
            self.request(agent=None, mode="clean", source_session_id=None)
        )

        self.assertEqual("future-agent", plan.agent)
        self.assertEqual("/mock/bin/omarchy-agent", plan.agent_argv[0])
        self.assertEqual("--inline", plan.agent_argv[1])
        self.assertEqual("--prompt", plan.agent_argv[2])

    def test_known_default_agent_uses_direct_adapter(self) -> None:
        launcher = AgentLauncher(
            self.store,
            executable_resolver=self.executable,
            default_agent_resolver=lambda: "copilot",
        )

        plan = launcher.prepare(
            self.request(agent=None, mode="clean", source_session_id=None)
        )

        self.assertEqual("copilot", plan.agent)
        self.assertEqual("/mock/bin/copilot", plan.agent_argv[0])
        self.assertNotIn("omarchy-agent", plan.agent_argv[0])

    def test_changed_context_is_rejected_after_preview(self) -> None:
        preview = ContextBuilder(self.store).build(
            mode="session", session_id=self.source.id
        )
        self.store.add_checkpoint(self.source.id, pending=["Changed after preview"])
        count_before = len(self.store.list_sessions())

        with self.assertRaises(InvalidContextRequestError):
            self.launcher.prepare(
                self.request(expected_context_fingerprint=preview.fingerprint)
            )

        self.assertEqual(count_before, len(self.store.list_sessions()))

    def test_launch_wraps_agent_in_omarchy_terminal(self) -> None:
        process_factory = Mock()
        launcher = AgentLauncher(
            self.store,
            executable_resolver=self.executable,
            process_factory=process_factory,
            now=lambda: datetime(2026, 8, 29, 15, 0, tzinfo=UTC),
        )
        plan = launcher.launch(
            self.request(mode="clean", source_session_id=None)
        )

        outer_argv = process_factory.call_args.args[0]
        self.assertEqual("/mock/bin/omarchy-launch-tui", outer_argv[0])
        self.assertIn("--app-id=org.omarchy.agent", outer_argv)
        self.assertEqual(list(plan.agent_argv), outer_argv[-len(plan.agent_argv) :])
        self.assertEqual(str(self.project.resolve()), process_factory.call_args.kwargs["cwd"])
        self.assertTrue(process_factory.call_args.kwargs["start_new_session"])
