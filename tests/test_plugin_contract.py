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
