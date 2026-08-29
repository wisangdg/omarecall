from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import TestCase

from omarecall.errors import InvalidImportError
from omarecall.importer import ConversationImporter


class ConversationImporterTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.root = Path(self.temp_dir)
        self.importer = ConversationImporter()

    def write_bytes(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def write_json(self, name: str, value: object) -> Path:
        return self.write_bytes(name, json.dumps(value).encode())

    def test_loads_utf8_bom_markdown_without_modifying_body(self) -> None:
        path = self.write_bytes("Design notes.md", b"\xef\xbb\xbf# Existing chat\n\nHello\n")

        imported = self.importer.load(path)

        self.assertEqual("Design notes", imported.title)
        self.assertEqual("# Existing chat\n\nHello", imported.transcript)
        self.assertEqual("Design notes.md", imported.source_name)
        self.assertEqual("markdown", imported.source_format)
        self.assertEqual(0, imported.message_count)
        self.assertEqual((), imported.warnings)

    def test_loads_plain_text(self) -> None:
        imported = self.importer.load(self.write_bytes("talk.txt", "halo dunia".encode()))
        self.assertEqual("text", imported.source_format)
        self.assertEqual("halo dunia", imported.transcript)

    def test_normalizes_generic_messages_and_ignores_system_and_tool(self) -> None:
        path = self.write_json(
            "generic.json",
            {
                "title": "API discussion",
                "messages": [
                    {"role": "system", "content": "secret setup"},
                    {"role": "user", "content": "Build it"},
                    {"role": "assistant", "content": {"parts": ["Done", " safely"]}},
                    {"role": "tool", "content": "raw output"},
                ],
            },
        )
        imported = self.importer.load(path)

        self.assertEqual("API discussion", imported.title)
        self.assertEqual("generic-json", imported.source_format)
        self.assertEqual(2, imported.message_count)
        self.assertEqual("## User\n\nBuild it\n\n## Assistant\n\nDone safely", imported.transcript)
        self.assertNotIn("secret setup", imported.transcript)
        self.assertEqual(2, len(imported.warnings))

    def test_top_level_array_is_allowed_only_when_it_is_a_message_array(self) -> None:
        valid = self.write_json(
            "messages.json",
            [{"role": "human", "content": "Hi"}, {"role": "ai", "content": "Hello"}],
        )
        self.assertEqual(2, self.importer.load(valid).message_count)

        multiple = self.write_json(
            "export.json",
            [
                {"title": "one", "messages": [{"role": "user", "content": "a"}]},
                {"title": "two", "messages": [{"role": "user", "content": "b"}]},
            ],
        )
        with self.assertRaisesRegex(InvalidImportError, "one conversation"):
            self.importer.load(multiple)

    def test_loads_only_active_chatgpt_branch_in_chronological_order(self) -> None:
        path = self.write_json(
            "chatgpt.json",
            {
                "title": "Branching chat",
                "current_node": "answer",
                "mapping": {
                    "root": {"id": "root", "parent": None, "message": None},
                    "question": {
                        "id": "question", "parent": "root",
                        "message": {"author": {"role": "user"}, "content": {"parts": ["Why?"]}},
                    },
                    "unused": {
                        "id": "unused", "parent": "question",
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["Old answer"]},
                        },
                    },
                    "answer": {
                        "id": "answer", "parent": "question",
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["Because."]},
                        },
                    },
                },
            },
        )
        imported = self.importer.load(path)

        self.assertEqual("chatgpt-json", imported.source_format)
        self.assertIn("Why?", imported.transcript)
        self.assertIn("Because.", imported.transcript)
        self.assertNotIn("Old answer", imported.transcript)

    def test_chatgpt_parent_cycle_is_rejected(self) -> None:
        path = self.write_json(
            "cycle.json",
            {"current_node": "a", "mapping": {
                "a": {"parent": "b", "message": None},
                "b": {"parent": "a", "message": None},
            }},
        )
        with self.assertRaisesRegex(InvalidImportError, "cycle"):
            self.importer.load(path)

    def test_loads_claude_chat_messages(self) -> None:
        path = self.write_json(
            "claude.json",
            {"name": "Claude export", "chat_messages": [
                {"sender": "human", "text": "Review this"},
                {"sender": "assistant", "text": "Looks good"},
            ]},
        )
        imported = self.importer.load(path)

        self.assertEqual("Claude export", imported.title)
        self.assertEqual("claude-json", imported.source_format)
        self.assertEqual(2, imported.message_count)
        self.assertIn("## User\n\nReview this", imported.transcript)

    def test_rejects_symlink_even_when_target_is_regular(self) -> None:
        target = self.write_bytes("target.md", b"safe")
        link = self.root / "link.md"
        link.symlink_to(target)
        with self.assertRaisesRegex(InvalidImportError, "symbolic link"):
            self.importer.load(link)

    def test_rejects_non_regular_source(self) -> None:
        directory = self.root / "folder.md"
        directory.mkdir()
        with self.assertRaisesRegex(InvalidImportError, "regular file"):
            self.importer.load(directory)

    def test_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are not supported")
        fifo = self.root / "stream.txt"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(InvalidImportError, "regular file"):
            self.importer.load(fifo)

    def test_rejects_oversize_source(self) -> None:
        path = self.write_bytes("large.txt", b"a" * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(InvalidImportError, "2 MiB"):
            self.importer.load(path)

    def test_rejects_invalid_text_and_extensions(self) -> None:
        cases = {"empty.txt": b"  \n", "nul.md": b"hello\x00world",
                 "binary.txt": b"\xff\xfe", "chat.csv": b"user,hello"}
        for name, body in cases.items():
            with self.subTest(name=name), self.assertRaises(InvalidImportError):
                self.importer.load(self.write_bytes(name, body))

    def test_rejects_malformed_unsupported_and_deep_json(self) -> None:
        malformed = self.write_bytes("bad.json", b"{")
        unsupported = self.write_json("settings.json", {"theme": "dark"})
        deep: object = "message"
        for _ in range(70):
            deep = [deep]
        deeply_nested = self.write_json("deep.json", {"messages": deep})
        for path in (malformed, unsupported, deeply_nested):
            with self.subTest(path=path.name), self.assertRaises(InvalidImportError):
                self.importer.load(path)

    def test_rejects_nul_introduced_by_json_decoding(self) -> None:
        path = self.write_bytes(
            "nul.json",
            b'{"messages":[{"role":"user","content":"hello\\u0000world"}]}',
        )

        with self.assertRaisesRegex(InvalidImportError, "NUL"):
            self.importer.load(path)

    def test_open_uses_no_follow_when_platform_supports_it(self) -> None:
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW is not available")
        path = self.write_bytes("chat.md", b"hello")
        real_open = os.open
        flags_seen: list[int] = []

        def recording_open(file: os.PathLike[str], flags: int) -> int:
            flags_seen.append(flags)
            return real_open(file, flags)

        from unittest.mock import patch
        with patch("omarecall.importer.os.open", side_effect=recording_open):
            self.importer.load(path)
        self.assertTrue(flags_seen[0] & os.O_NOFOLLOW)

    def test_open_fails_closed_without_no_follow_support(self) -> None:
        path = self.write_bytes("chat.md", b"hello")
        from unittest.mock import patch

        with patch("omarecall.importer._NOFOLLOW_FLAG", None):
            with self.assertRaisesRegex(InvalidImportError, "symbolic links safely"):
                self.importer.load(path)
