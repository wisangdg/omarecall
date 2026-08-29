"""Safe, best-effort parsing for externally-created conversation files."""

from __future__ import annotations

import errno
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omarecall.errors import InvalidImportError

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64
_NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", None)
_IGNORED_ROLES = frozenset({"developer", "function", "system", "tool"})
_USER_ROLES = frozenset({"human", "user"})
_ASSISTANT_ROLES = frozenset({"ai", "assistant", "model"})


@dataclass(frozen=True, slots=True)
class ImportedConversation:
    """A single external conversation normalized for OmaRecall."""

    title: str
    transcript: str
    source_name: str
    source_format: str
    message_count: int
    warnings: tuple[str, ...]


class ConversationImporter:
    """Load one bounded local file without following symbolic links."""

    def load(self, path: Path) -> ImportedConversation:
        source = Path(path)
        extension = source.suffix.casefold()
        if extension not in {".json", ".md", ".txt"}:
            raise InvalidImportError("Only .md, .txt, and .json files are supported")

        raw = self._read_regular_file(source)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InvalidImportError("The import must be valid UTF-8 text") from error
        if "\x00" in text:
            raise InvalidImportError("The import contains a NUL byte")
        text = text.strip()
        if not text:
            raise InvalidImportError("The import is empty")

        if extension == ".md":
            return self._plain(source, text, "markdown")
        if extension == ".txt":
            return self._plain(source, text, "text")
        return self._json(source, text)

    @staticmethod
    def _read_regular_file(path: Path) -> bytes:
        if _NOFOLLOW_FLAG is None:
            raise InvalidImportError(
                "This platform cannot reject symbolic links safely"
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | _NOFOLLOW_FLAG
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise InvalidImportError("The import cannot be a symbolic link") from error
            raise InvalidImportError(f"Unable to open import source: {error.strerror}") from error

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise InvalidImportError("The import source must be a regular file")
            if metadata.st_size > MAX_SOURCE_BYTES:
                raise InvalidImportError("The import exceeds the 2 MiB size limit")

            chunks: list[bytes] = []
            remaining = MAX_SOURCE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > MAX_SOURCE_BYTES:
                raise InvalidImportError("The import exceeds the 2 MiB size limit")
            return content
        except OSError as error:
            raise InvalidImportError(f"Unable to read import source: {error.strerror}") from error
        finally:
            os.close(descriptor)

    @staticmethod
    def _plain(path: Path, text: str, source_format: str) -> ImportedConversation:
        return ImportedConversation(
            title=path.stem,
            transcript=text,
            source_name=path.name,
            source_format=source_format,
            message_count=0,
            warnings=(),
        )

    def _json(self, path: Path, text: str) -> ImportedConversation:
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as error:
            raise InvalidImportError("The import contains malformed or overly deep JSON") from error
        self._reject_deep_json(value)

        if isinstance(value, list):
            if not self._is_message_array(value):
                raise InvalidImportError(
                    "Import exactly one conversation, not a multi-conversation export"
                )
            return self._from_messages(path, value, path.stem, "generic-json")
        if not isinstance(value, dict):
            raise InvalidImportError("The JSON does not contain a supported conversation")
        if "mapping" in value or "current_node" in value:
            return self._from_chatgpt(path, value)
        if "chat_messages" in value:
            messages = value.get("chat_messages")
            if not isinstance(messages, list):
                raise InvalidImportError("Claude chat_messages must be an array")
            return self._from_messages(
                path, messages, self._title(value, path), "claude-json", claude=True
            )
        if "messages" in value:
            messages = value.get("messages")
            if not isinstance(messages, list):
                raise InvalidImportError("Conversation messages must be an array")
            return self._from_messages(path, messages, self._title(value, path), "generic-json")
        raise InvalidImportError("The JSON does not contain a supported conversation")

    @staticmethod
    def _reject_deep_json(value: Any) -> None:
        pending: list[tuple[Any, int]] = [(value, 1)]
        while pending:
            item, depth = pending.pop()
            if depth > MAX_JSON_DEPTH:
                raise InvalidImportError("The JSON nesting is too deep")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)

    @staticmethod
    def _is_message_array(value: list[Any]) -> bool:
        return bool(value) and all(
            isinstance(item, dict) and any(key in item for key in ("role", "sender", "author"))
            for item in value
        )

    def _from_chatgpt(self, path: Path, value: dict[str, Any]) -> ImportedConversation:
        mapping = value.get("mapping")
        current = value.get("current_node")
        if not isinstance(mapping, dict) or not isinstance(current, str) or current not in mapping:
            raise InvalidImportError("ChatGPT JSON requires a valid mapping and current_node")

        branch: list[dict[str, Any]] = []
        visited: set[str] = set()
        while current is not None:
            if current in visited:
                raise InvalidImportError("ChatGPT active branch contains a parent cycle")
            visited.add(current)
            node = mapping.get(current)
            if not isinstance(node, dict):
                raise InvalidImportError("ChatGPT active branch references a missing node")
            message = node.get("message")
            if isinstance(message, dict):
                branch.append(message)
            parent = node.get("parent")
            if parent is not None and not isinstance(parent, str):
                raise InvalidImportError("ChatGPT node has an invalid parent")
            current = parent
        branch.reverse()
        return self._from_messages(
            path, branch, self._title(value, path), "chatgpt-json", chatgpt=True
        )

    def _from_messages(
        self,
        path: Path,
        messages: list[Any],
        title: str,
        source_format: str,
        *,
        claude: bool = False,
        chatgpt: bool = False,
    ) -> ImportedConversation:
        sections: list[str] = []
        warnings: list[str] = []
        for index, message in enumerate(messages, start=1):
            if not isinstance(message, dict):
                warnings.append(f"Skipped malformed message {index}")
                continue
            role, content = self._message_fields(message, claude=claude, chatgpt=chatgpt)
            normalized_role = role.casefold().strip()
            if normalized_role in _IGNORED_ROLES:
                warnings.append(f"Skipped {normalized_role} message {index}")
                continue
            if normalized_role in _USER_ROLES:
                heading = "User"
            elif normalized_role in _ASSISTANT_ROLES:
                heading = "Assistant"
            else:
                warnings.append(f"Skipped message {index} with unsupported role")
                continue
            normalized_content = content.strip()
            if not normalized_content:
                warnings.append(f"Skipped empty {normalized_role} message {index}")
                continue
            sections.append(f"## {heading}\n\n{normalized_content}")

        if not sections:
            raise InvalidImportError("The conversation contains no user or assistant messages")
        transcript = "\n\n".join(sections)
        if "\x00" in transcript:
            raise InvalidImportError("The imported conversation contains a NUL byte")
        return ImportedConversation(
            title=title,
            transcript=transcript,
            source_name=path.name,
            source_format=source_format,
            message_count=len(sections),
            warnings=tuple(warnings),
        )

    @classmethod
    def _message_fields(
        cls, message: dict[str, Any], *, claude: bool, chatgpt: bool
    ) -> tuple[str, str]:
        if claude:
            role = message.get("sender")
            content = message.get("text")
        elif chatgpt:
            author = message.get("author")
            role = author.get("role") if isinstance(author, dict) else None
            content = message.get("content")
        else:
            role = message.get("role", message.get("sender"))
            author = message.get("author")
            if role is None and isinstance(author, dict):
                role = author.get("role")
            content = message.get("content", message.get("text"))
        return (role if isinstance(role, str) else "", cls._content_text(content))

    @classmethod
    def _content_text(cls, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(cls._content_text(part) for part in content)
        if isinstance(content, dict):
            for key in ("parts", "text", "content"):
                if key in content:
                    return cls._content_text(content[key])
        return ""

    @staticmethod
    def _title(value: dict[str, Any], path: Path) -> str:
        for key in ("title", "name"):
            title = value.get(key)
            if isinstance(title, str) and title.strip():
                return title.strip()
        return path.stem
