from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase


class PluginContractTests(TestCase):
    root = Path(__file__).parents[1]

    def test_manifest_exposes_panel_and_bar_widget(self) -> None:
        manifest = json.loads((self.root / "manifest.json").read_text())

        self.assertEqual("wdg.omarecall", manifest["id"])
        self.assertEqual({"panel", "bar-widget"}, set(manifest["kinds"]))
        for entry_point in manifest["entryPoints"].values():
            self.assertTrue((self.root / entry_point).is_file())

    def test_versions_are_synchronized_across_metadata_files(self) -> None:
        import tomllib
        import omarecall

        manifest = json.loads((self.root / "manifest.json").read_text())
        pyproject = tomllib.loads((self.root / "pyproject.toml").read_text())

        version = manifest["version"]
        self.assertEqual("0.3.0", version)
        self.assertEqual(version, pyproject["project"]["version"])
        self.assertEqual(version, omarecall.__version__)

    def test_panel_contract_has_keyboard_accessibility_and_bounded_history(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        self.assertIn("Accessible.name", panel)
        self.assertIn("Qt.Key_Escape", panel)
        self.assertIn("Qt.Key_Down", panel)
        self.assertIn('"--limit", "100"', panel)
        self.assertIn("ListView", panel)
        self.assertIn("ScrollView", panel)

    def test_qml_processes_use_argument_arrays_not_shell_commands(self) -> None:
        sources = "\n".join(
            path.read_text() for path in (self.root / "BarWidget.qml", self.root / "Panel.qml")
        )

        self.assertNotIn("bash -c", sources)
        self.assertNotIn("sh -c", sources)
        self.assertNotIn("shell: true", sources)

    def test_panel_uses_native_omarchy_directory_picker(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        self.assertIn('"omarchy-file-select"', panel)
        self.assertIn('"--directory"', panel)
        self.assertIn('Accessible.name: "Browse project directory"', panel)
        self.assertIn("property bool reopenAfterPicker", panel)
        self.assertIn("directoryPickerLaunchTimer.restart()", panel)
        self.assertIn('Quickshell.env("OMARECALL_DEFAULT_PROJECT")', panel)
        self.assertIn('text: root.defaultProjectDirectory', panel)
        self.assertNotIn('"/development/projects"', panel)

    def test_panel_imports_external_conversations_with_native_file_picker(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        self.assertIn('text: importPicker.running ? "Opening…" : "Import conversation…"', panel)
        self.assertIn('Accessible.name: "Import an external AI conversation"', panel)
        self.assertIn('"--extensions", "md txt json"', panel)
        self.assertIn("conversationPickerLaunchTimer.restart()", panel)
        self.assertIn(
            '[root.cli, "import", "--file", selectedPath, "--project",',
            panel,
        )
        self.assertIn('"--max-import-chars", "200000"', panel)
        self.assertIn("root.detail.imported_conversation", panel)
        self.assertIn("importOut.text", panel)
        self.assertIn("summary.warnings", panel)

    def test_panel_offers_effective_omarchy_agents_in_accessible_selector(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        for key, label in {
            "": "Omarchy default",
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "Copilot",
            "crush": "Crush",
            "grok": "Grok",
            "omp": "Oh My Pi",
            "opencode": "OpenCode",
            "pi": "Pi",
            "agy": "Antigravity",
        }.items():
            self.assertIn(f'value: "{key}", label: "{label}"', panel)
        self.assertIn("Ui.Dropdown", panel)
        self.assertNotIn("ComboBox {", panel)
        self.assertIn('Accessible.name: "Agent launcher"', panel)
        self.assertIn('if (root.selectedAgent !== "")', panel)
        self.assertNotIn('value: "gemini"', panel)
        self.assertNotIn('model: ["codex", "claude", "opencode"]', panel)

    def test_panel_polish_uses_clear_memory_and_session_action_hierarchy(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        self.assertIn("function continuationGoal(title)", panel)
        self.assertIn('text: "Memory for the new agent"', panel)
        self.assertIn('text: "Project memory"', panel)
        self.assertIn('text: "Selected session"', panel)
        self.assertIn('text: "No memory"', panel)
        self.assertIn('text: "Manage selected session"', panel)
        self.assertIn("foreground: root.urgent", panel)
        self.assertIn("enabled: root.opened && !agentSelector.popupOpen", panel)
        self.assertIn('iconText: "󰅙"', panel)
        self.assertIn('tooltipText: "Close"', panel)
        self.assertIn("iconSize: Style.font.body", panel)
        self.assertNotIn('text: "Close"', panel)

    def test_preview_warns_before_unattended_agent_launch(self) -> None:
        panel = (self.root / "Panel.qml").read_text()

        self.assertIn("Unattended mode", panel)
        self.assertIn("auto-approve actions", panel)
        self.assertIn("Recalled memory is untrusted", panel)
        self.assertIn('Accessible.name: "Unattended agent safety warning"', panel)
