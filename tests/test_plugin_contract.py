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
