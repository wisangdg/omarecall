"""Human-readable, local-first storage for OmaRecall sessions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, ParamSpec, TypeVar

from omarecall.errors import (
    InvalidDataPathError,
    InvalidSessionError,
    SessionExistsError,
    SessionNotFoundError,
    StoreCorruptError,
)

SCHEMA_VERSION = 1
VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VALID_STATUS = {"active", "completed", "interrupted", "archived"}
NOTE_SECTIONS = ("Goal", "Completed", "Decisions", "Pending", "Files", "Warnings")
MAX_STORE_FILE_BYTES = 4 * 1024 * 1024
MAX_IMPORTED_TRANSCRIPT_BYTES = 2 * 1024 * 1024
MAX_IMPORTED_TITLE_CHARS = 200
VALID_SOURCE_FORMAT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}$")

_P = ParamSpec("_P")
_R = TypeVar("_R")
_ACTIVE_LOCK_DESCRIPTORS: set[int] = set()
_DESCRIPTOR_REGISTRY_LOCK = threading.Lock()


def _prepare_store_lock_fork() -> None:
    """Freeze the descriptor registry while the process is being forked."""
    _DESCRIPTOR_REGISTRY_LOCK.acquire()


def _finish_store_lock_fork_in_parent() -> None:
    _DESCRIPTOR_REGISTRY_LOCK.release()


def _finish_store_lock_fork_in_child() -> None:
    """Drop file descriptions whose flock ownership came from the parent."""
    try:
        for descriptor in _ACTIVE_LOCK_DESCRIPTORS:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _ACTIVE_LOCK_DESCRIPTORS.clear()
    finally:
        _DESCRIPTOR_REGISTRY_LOCK.release()


def _open_registered_lock_descriptor(path: Path, flags: int) -> int:
    """Open and register a lock descriptor atomically with respect to fork."""
    with _DESCRIPTOR_REGISTRY_LOCK:
        descriptor = os.open(path, flags, 0o600)
        _ACTIVE_LOCK_DESCRIPTORS.add(descriptor)
        return descriptor


def _close_registered_lock_descriptor(descriptor: int) -> None:
    """Unregister and close a lock descriptor atomically with respect to fork."""
    with _DESCRIPTOR_REGISTRY_LOCK:
        _ACTIVE_LOCK_DESCRIPTORS.discard(descriptor)
        os.close(descriptor)


os.register_at_fork(
    before=_prepare_store_lock_fork,
    after_in_parent=_finish_store_lock_fork_in_parent,
    after_in_child=_finish_store_lock_fork_in_child,
)


def _store_locked(
    method: Callable[_P, _R],
) -> Callable[_P, _R]:
    """Serialize a complete public store operation across threads and processes."""

    @wraps(method)
    def locked(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        store = args[0]
        with store._store_lock():
            return method(*args, **kwargs)

    return locked


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise StoreCorruptError(f"Invalid timestamp in session metadata: {value}") from exc


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _require_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise InvalidSessionError(f"{name} must not be empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Stable metadata stored beside each human-readable note."""

    id: str
    schema_version: int
    project_id: str
    project_name: str
    project_path: str
    agent: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    pinned: bool = False
    source_name: str | None = None
    source_format: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = _format_time(self.created_at)
        result["updated_at"] = _format_time(self.updated_at)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionMetadata:
        required = {
            "id",
            "schema_version",
            "project_id",
            "project_name",
            "project_path",
            "agent",
            "title",
            "status",
            "created_at",
            "updated_at",
        }
        missing = sorted(required.difference(value))
        if missing:
            raise StoreCorruptError(f"Session metadata is missing: {', '.join(missing)}")
        if value["schema_version"] != SCHEMA_VERSION:
            raise StoreCorruptError(
                f"Unsupported session schema version: {value['schema_version']}"
            )
        if value["status"] not in VALID_STATUS:
            raise StoreCorruptError(f"Invalid stored session status: {value['status']}")
        if not VALID_SESSION_ID.fullmatch(str(value["id"])):
            raise StoreCorruptError(f"Invalid stored session ID: {value['id']}")
        if not VALID_SESSION_ID.fullmatch(str(value["project_id"])):
            raise StoreCorruptError(f"Invalid stored project ID: {value['project_id']}")
        source_name = value.get("source_name")
        source_format = value.get("source_format")
        if (source_name is None) != (source_format is None):
            raise StoreCorruptError("Imported session provenance is incomplete")
        if source_name is not None:
            if (
                not isinstance(source_name, str)
                or not source_name
                or source_name != Path(source_name).name
                or "/" in source_name
                or "\\" in source_name
                or any(ord(character) < 32 for character in source_name)
            ):
                raise StoreCorruptError("Invalid imported session source name")
            if not isinstance(source_format, str) or not VALID_SOURCE_FORMAT.fullmatch(
                source_format
            ):
                raise StoreCorruptError("Invalid imported session source format")
        return cls(
            id=str(value["id"]),
            schema_version=int(value["schema_version"]),
            project_id=str(value["project_id"]),
            project_name=str(value["project_name"]),
            project_path=str(value["project_path"]),
            agent=str(value["agent"]),
            title=str(value["title"]),
            status=str(value["status"]),
            created_at=_parse_time(str(value["created_at"])),
            updated_at=_parse_time(str(value["updated_at"])),
            pinned=bool(value.get("pinned", False)),
            source_name=source_name,
            source_format=source_format,
        )


class SessionStore:
    """Manage session notes and rebuildable JSON indexes under one private root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self._thread_lock = threading.RLock()
        self._lock_state = threading.local()
        self._lock_pid = os.getpid()

    @classmethod
    def default(cls) -> SessionStore:
        data_home = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        return cls(data_home / "omarecall")

    @property
    def projects_path(self) -> Path:
        return self.root / "projects.json"

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @_store_locked
    def initialize(self) -> None:
        self._secure_mkdir(self.root)
        self._secure_mkdir(self.root / "projects")
        if not self.projects_path.exists():
            self._write_json(
                self.projects_path,
                {"schema_version": SCHEMA_VERSION, "projects": {}},
            )
        else:
            self._reject_symlink(self.projects_path)
        if not self.index_path.exists():
            self._write_index([])
        else:
            self._reject_symlink(self.index_path)

    @_store_locked
    def create_session(
        self,
        *,
        project_path: Path | str,
        agent: str,
        goal: str,
        title: str | None = None,
        now: datetime | None = None,
        session_id: str | None = None,
    ) -> SessionMetadata:
        self.initialize()
        project_id, project_name, resolved_project = self.project_identity(project_path)
        agent = _require_text("agent", agent)
        goal = _require_text("goal", goal)
        timestamp = (now or _utc_now()).astimezone(UTC)
        generated_id = session_id or self._new_session_id(timestamp)
        self._validate_session_id(generated_id)

        if any(item.id == generated_id for item in self.list_sessions()):
            raise SessionExistsError(f"Session already exists: {generated_id}")

        session_dir = self._session_dir(project_id, generated_id)
        if session_dir.exists():
            raise SessionExistsError(f"Session already exists: {generated_id}")

        self._secure_mkdir(session_dir)
        metadata = SessionMetadata(
            id=generated_id,
            schema_version=SCHEMA_VERSION,
            project_id=project_id,
            project_name=project_name,
            project_path=str(resolved_project),
            agent=agent,
            title=(title or goal).strip(),
            status="active",
            created_at=timestamp,
            updated_at=timestamp,
            pinned=False,
        )
        sections = {name: [] for name in NOTE_SECTIONS}
        sections["Goal"] = [goal]
        self._write_json(session_dir / "meta.json", metadata.to_dict())
        self._atomic_write(session_dir / "note.md", self._render_note(metadata, sections))
        self._upsert_project(metadata)
        self._upsert_index(metadata)
        return metadata

    @_store_locked
    def create_imported_session(
        self,
        *,
        project_path: Path | str,
        title: str,
        transcript: str,
        source_name: str,
        source_format: str,
        now: datetime | None = None,
        session_id: str | None = None,
    ) -> SessionMetadata:
        """Create a completed external session backed by a private transcript."""
        title = self._import_title(title)
        if not isinstance(transcript, str) or not transcript.strip():
            raise InvalidSessionError("transcript must not be empty")
        if "\x00" in transcript:
            raise InvalidSessionError("transcript must not contain NUL bytes")
        if len(transcript.encode("utf-8")) > MAX_IMPORTED_TRANSCRIPT_BYTES:
            raise InvalidSessionError("transcript must not exceed 2 MiB")
        safe_source_name = self._source_basename(source_name)
        safe_source_format = _require_text("source_format", source_format).lower()
        if not VALID_SOURCE_FORMAT.fullmatch(safe_source_format):
            raise InvalidSessionError("source_format contains unsafe characters")

        self.initialize()
        project_id, project_name, resolved_project = self.project_identity(project_path)
        timestamp = (now or _utc_now()).astimezone(UTC)
        generated_id = session_id or self._new_session_id(timestamp)
        self._validate_session_id(generated_id)
        if any(item.id == generated_id for item in self.list_sessions()):
            raise SessionExistsError(f"Session already exists: {generated_id}")

        session_dir = self._session_dir(project_id, generated_id)
        if session_dir.exists():
            raise SessionExistsError(f"Session already exists: {generated_id}")
        self._secure_mkdir(session_dir.parent)
        staging_dir = session_dir.parent / (
            f".{generated_id}.import-{secrets.token_hex(6)}"
        )
        self._secure_mkdir(staging_dir)
        metadata = SessionMetadata(
            id=generated_id,
            schema_version=SCHEMA_VERSION,
            project_id=project_id,
            project_name=project_name,
            project_path=str(resolved_project),
            agent="external",
            title=title,
            status="completed",
            created_at=timestamp,
            updated_at=timestamp,
            pinned=False,
            source_name=safe_source_name,
            source_format=safe_source_format,
        )
        sections = {name: [] for name in NOTE_SECTIONS}
        sections["Goal"] = [f"Imported conversation: {title}"]
        sections["Completed"] = [
            f"Imported from {safe_source_name} ({safe_source_format})"
        ]
        try:
            self._atomic_write(staging_dir / "import.md", transcript)
            self._write_json(staging_dir / "meta.json", metadata.to_dict())
            self._atomic_write(
                staging_dir / "note.md", self._render_note(metadata, sections)
            )
            if session_dir.exists() or session_dir.is_symlink():
                raise SessionExistsError(f"Session already exists: {generated_id}")
            os.replace(staging_dir, session_dir)
            self._upsert_project(metadata)
            self._upsert_index(metadata)
        except BaseException:
            self._cleanup_import_session(staging_dir)
            self._cleanup_import_session(session_dir)
            raise
        return metadata

    @staticmethod
    def project_identity(project_path: Path | str) -> tuple[str, str, Path]:
        """Return the stable ID, display slug, and resolved path for a project."""
        resolved = Path(project_path).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise InvalidSessionError(f"Project path is not a directory: {resolved}")
        name = _slug(resolved.name)
        digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:10]
        return f"{name}-{digest}", name, resolved

    @_store_locked
    def get_session(self, session_id: str) -> dict[str, Any]:
        metadata, session_dir = self._find_session(session_id)
        note_path = session_dir / "note.md"
        self._reject_symlink(note_path)
        try:
            note = self._read_regular_text(note_path)
        except FileNotFoundError as exc:
            raise StoreCorruptError(f"Session note is missing: {session_id}") from exc
        return {**metadata.to_dict(), "note": note}

    @_store_locked
    def get_session_metadata(self, session_id: str) -> SessionMetadata:
        """Return validated session metadata without loading its note."""
        metadata, _ = self._find_session(session_id)
        return metadata

    @_store_locked
    def get_session_sections(
        self, session_id: str
    ) -> tuple[SessionMetadata, dict[str, list[str]]]:
        """Return validated metadata and parsed human-readable note sections."""
        metadata, session_dir = self._find_session(session_id)
        note_path = session_dir / "note.md"
        self._reject_symlink(note_path)
        try:
            note = self._read_regular_text(note_path)
        except FileNotFoundError as exc:
            raise StoreCorruptError(f"Session note is missing: {session_id}") from exc
        return metadata, self._parse_note(note)

    @_store_locked
    def get_imported_conversation(
        self, session_id: str, *, max_chars: int | None = None
    ) -> str | None:
        """Return an imported transcript, or ``None`` for a native session."""
        metadata, session_dir = self._find_session(session_id)
        if metadata.source_name is None:
            return None
        if max_chars is not None and max_chars < 0:
            raise InvalidSessionError("max_chars must not be negative")
        import_path = session_dir / "import.md"
        try:
            transcript = self._read_regular_text(
                import_path, max_bytes=MAX_IMPORTED_TRANSCRIPT_BYTES
            )
        except FileNotFoundError as exc:
            raise StoreCorruptError(
                f"Imported conversation is missing: {session_id}"
            ) from exc
        if max_chars is None:
            return transcript
        return transcript[:max_chars]

    @_store_locked
    def save_context_packet(self, session_id: str, packet: str) -> Path:
        """Persist a redacted launch packet beside its new session note."""
        if not packet:
            raise InvalidSessionError("Context packet must not be empty")
        _, session_dir = self._find_session(session_id)
        context_path = session_dir / "context.md"
        self._atomic_write(context_path, packet)
        return context_path

    @_store_locked
    def list_sessions(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[SessionMetadata]:
        self.initialize()
        if status is not None and status not in VALID_STATUS:
            raise InvalidSessionError(f"Invalid session status: {status}")
        index = self._read_json(self.index_path)
        sessions = [SessionMetadata.from_dict(item) for item in index.get("sessions", [])]
        if project_id is not None:
            sessions = [item for item in sessions if item.project_id == project_id]
        if status is not None:
            sessions = [item for item in sessions if item.status == status]
        sessions = sorted(sessions, key=lambda item: item.updated_at, reverse=True)
        if limit is not None:
            if limit < 0:
                raise InvalidSessionError("limit must not be negative")
            sessions = sessions[:limit]
        return sessions

    @_store_locked
    def add_checkpoint(
        self,
        session_id: str,
        *,
        completed: Iterable[str] = (),
        decisions: Iterable[str] = (),
        pending: Iterable[str] = (),
        resolve_pending: Iterable[str] = (),
        remove_pending: Iterable[str] = (),
        files: Iterable[str] = (),
        warnings: Iterable[str] = (),
        status: str | None = None,
        now: datetime | None = None,
    ) -> SessionMetadata:
        metadata, session_dir = self._find_session(session_id)
        if status is not None and status not in VALID_STATUS:
            raise InvalidSessionError(f"Invalid session status: {status}")
        note_path = session_dir / "note.md"
        self._reject_symlink(note_path)
        try:
            sections = self._parse_note(self._read_regular_text(note_path))
        except FileNotFoundError as exc:
            raise StoreCorruptError(f"Session note is missing: {session_id}") from exc

        additions = {
            "Completed": completed,
            "Decisions": decisions,
            "Pending": pending,
            "Files": files,
            "Warnings": warnings,
        }
        for section, values in additions.items():
            existing = sections[section]
            for value in values:
                cleaned = value.strip()
                if cleaned and cleaned not in existing:
                    existing.append(cleaned)

        # Exact text matching avoids resolving a different, similarly named task.
        # Repeated commands are harmless; only existing pending items are moved.
        resolved = {value.strip() for value in resolve_pending if value.strip()}
        removed = {value.strip() for value in remove_pending if value.strip()}
        for value in sections["Pending"]:
            if value in resolved and value not in sections["Completed"]:
                sections["Completed"].append(value)
        sections["Pending"] = [
            value for value in sections["Pending"] if value not in resolved | removed
        ]

        updated = SessionMetadata(
            id=metadata.id,
            schema_version=metadata.schema_version,
            project_id=metadata.project_id,
            project_name=metadata.project_name,
            project_path=metadata.project_path,
            agent=metadata.agent,
            title=metadata.title,
            status=status or metadata.status,
            created_at=metadata.created_at,
            updated_at=(now or _utc_now()).astimezone(UTC),
            pinned=metadata.pinned,
            source_name=metadata.source_name,
            source_format=metadata.source_format,
        )
        self._write_json(session_dir / "meta.json", updated.to_dict())
        self._atomic_write(note_path, self._render_note(updated, sections))
        self._upsert_index(updated)
        return updated

    @_store_locked
    def archive_session(
        self, session_id: str, *, now: datetime | None = None
    ) -> SessionMetadata:
        return self.add_checkpoint(session_id, status="archived", now=now)

    @_store_locked
    def mark_interrupted_if_active(
        self, session_id: str, *, now: datetime | None = None
    ) -> SessionMetadata:
        """Close an agent session that ended without writing a final checkpoint."""
        metadata = self.get_session_metadata(session_id)
        if metadata.status != "active":
            return metadata
        return self.add_checkpoint(session_id, status="interrupted", now=now)

    @_store_locked
    def set_pinned(
        self,
        session_id: str,
        pinned: bool,
        *,
        now: datetime | None = None,
    ) -> SessionMetadata:
        metadata, session_dir = self._find_session(session_id)
        _, sections = self.get_session_sections(session_id)
        updated = SessionMetadata(
            id=metadata.id,
            schema_version=metadata.schema_version,
            project_id=metadata.project_id,
            project_name=metadata.project_name,
            project_path=metadata.project_path,
            agent=metadata.agent,
            title=metadata.title,
            status=metadata.status,
            created_at=metadata.created_at,
            updated_at=(now or _utc_now()).astimezone(UTC),
            pinned=pinned,
            source_name=metadata.source_name,
            source_format=metadata.source_format,
        )
        self._write_json(session_dir / "meta.json", updated.to_dict())
        self._atomic_write(session_dir / "note.md", self._render_note(updated, sections))
        self._upsert_index(updated)
        return updated

    @_store_locked
    def delete_session(self, session_id: str) -> None:
        """Delete one known session without recursively following filesystem state."""
        metadata, session_dir = self._find_session(session_id)
        allowed_files = {"meta.json", "note.md", "context.md", "import.md"}
        children = list(session_dir.iterdir())
        for child in children:
            self._reject_symlink(child)
            if child.name not in allowed_files or not child.is_file():
                raise InvalidDataPathError(
                    f"Refusing to delete session with an unknown entry: {child}"
                )
        for child in children:
            child.unlink()
        session_dir.rmdir()
        sessions = [item for item in self.list_sessions() if item.id != metadata.id]
        self._write_index(sessions)

    @_store_locked
    def reindex(self) -> int:
        self._secure_mkdir(self.root)
        projects_root = self.root / "projects"
        self._secure_mkdir(projects_root)
        sessions: list[SessionMetadata] = []
        for metadata_path in projects_root.glob("*/sessions/*/meta.json"):
            if metadata_path.is_symlink():
                continue
            sessions.append(SessionMetadata.from_dict(self._read_json(metadata_path)))
        self._write_index(sessions)
        return len(sessions)

    def _find_session(self, session_id: str) -> tuple[SessionMetadata, Path]:
        self._validate_session_id(session_id)
        for metadata in self.list_sessions():
            if metadata.id == session_id:
                session_dir = self._session_dir(metadata.project_id, metadata.id)
                stored = SessionMetadata.from_dict(
                    self._read_json(session_dir / "meta.json")
                )
                return stored, session_dir
        raise SessionNotFoundError(f"Session not found: {session_id}")

    def _session_dir(self, project_id: str, session_id: str) -> Path:
        return self.root / "projects" / project_id / "sessions" / session_id

    def _new_session_id(self, timestamp: datetime) -> str:
        prefix = timestamp.strftime("%Y%m%dT%H%M%SZ")
        for _ in range(8):
            candidate = f"{prefix}-{secrets.token_hex(2)}"
            if not any(item.id == candidate for item in self.list_sessions()):
                return candidate
        raise SessionExistsError("Unable to generate a unique session ID")

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not VALID_SESSION_ID.fullmatch(session_id):
            raise InvalidSessionError(f"Invalid session ID: {session_id}")

    @staticmethod
    def _source_basename(source_name: str) -> str:
        if not isinstance(source_name, str):
            raise InvalidSessionError("source_name must be text")
        cleaned = _require_text("source_name", source_name)
        if any(ord(character) < 32 for character in cleaned):
            raise InvalidSessionError("source_name contains unsafe characters")
        try:
            basename = re.split(r"[/\\]", cleaned)[-1]
        except (TypeError, ValueError) as exc:
            raise InvalidSessionError("source_name is invalid") from exc
        if basename in {"", ".", ".."}:
            raise InvalidSessionError("source_name must include a file name")
        return basename

    def _cleanup_import_session(self, session_dir: Path) -> None:
        """Remove only known files from a newly-created failed import."""
        if not session_dir.exists() and not session_dir.is_symlink():
            return
        self._reject_symlink(session_dir)
        for child in list(session_dir.iterdir()):
            self._reject_symlink(child)
            if (
                child.name not in {"import.md", "meta.json", "note.md"}
                or not child.is_file()
            ):
                raise InvalidDataPathError(
                    f"Refusing to clean import with an unknown entry: {child}"
                )
        for child in list(session_dir.iterdir()):
            child.unlink()
        session_dir.rmdir()

    @staticmethod
    def _import_title(title: str) -> str:
        if not isinstance(title, str):
            raise InvalidSessionError("title must be text")
        cleaned = _require_text("title", title)
        if len(cleaned) > MAX_IMPORTED_TITLE_CHARS:
            raise InvalidSessionError("title must not exceed 200 characters")
        if any(not character.isprintable() for character in cleaned):
            raise InvalidSessionError("title must be one printable line")
        return cleaned

    def _upsert_project(self, metadata: SessionMetadata) -> None:
        value = self._read_json(self.projects_path)
        projects = value.setdefault("projects", {})
        projects[metadata.project_id] = {
            "id": metadata.project_id,
            "name": metadata.project_name,
            "path": metadata.project_path,
            "updated_at": _format_time(metadata.updated_at),
        }
        self._write_json(self.projects_path, value)

    def _upsert_index(self, metadata: SessionMetadata) -> None:
        value = self._read_json(self.index_path)
        sessions = [
            item for item in value.get("sessions", []) if item.get("id") != metadata.id
        ]
        sessions.append(metadata.to_dict())
        self._write_index(SessionMetadata.from_dict(item) for item in sessions)

    def _write_index(self, sessions: Iterable[SessionMetadata]) -> None:
        ordered = sorted(sessions, key=lambda item: item.updated_at, reverse=True)
        self._write_json(
            self.index_path,
            {
                "schema_version": SCHEMA_VERSION,
                "sessions": [item.to_dict() for item in ordered],
            },
        )

    @staticmethod
    def _render_note(
        metadata: SessionMetadata, sections: dict[str, list[str]]
    ) -> str:
        lines = [
            "---",
            "note_format: 2",
            f"id: {metadata.id}",
            f"project: {metadata.project_name}",
            f"agent: {metadata.agent}",
            f"status: {metadata.status}",
            f"created_at: {_format_time(metadata.created_at)}",
            f"updated_at: {_format_time(metadata.updated_at)}",
            f"pinned: {'true' if metadata.pinned else 'false'}",
            "---",
            "",
        ]
        for section in NOTE_SECTIONS:
            lines.append(f"## {section}")
            values = sections.get(section, [])
            for value in values:
                first, *continuation = value.split("\n")
                if section == "Goal" and not first.startswith(("## ", "  ", "- ")):
                    lines.append(first)
                else:
                    lines.append(f"- {first}")
                lines.extend(f"  {line}" for line in continuation)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse_note(note: str) -> dict[str, list[str]]:
        sections = {name: [] for name in NOTE_SECTIONS}
        current: str | None = None
        lines = note.replace("\r\n", "\n").split("\n")
        version_two = False
        if lines and lines[0] == "---":
            try:
                end = lines.index("---", 1)
            except ValueError as exc:
                raise StoreCorruptError("Session note has unclosed frontmatter") from exc
            version_two = "note_format: 2" in lines[1:end]
            lines = lines[end + 1:]
        for line in lines:
            if version_two and current is not None and line.startswith("  "):
                if sections[current]:
                    sections[current][-1] += "\n" + line[2:]
                else:
                    sections[current].append(line[2:])
                continue
            if line.startswith("## "):
                candidate = line[3:].strip()
                current = candidate if candidate in sections else None
                continue
            if current is None or not line.strip():
                continue
            value = line[2:] if line.startswith("- ") else line
            if version_two or value not in sections[current]:
                sections[current].append(value)
        return sections

    def _read_json(self, path: Path) -> dict[str, Any]:
        self._reject_symlink(path)
        try:
            value = json.loads(self._read_regular_text(path))
        except FileNotFoundError as exc:
            raise StoreCorruptError(f"Required store file is missing: {path}") from exc
        except json.JSONDecodeError as exc:
            raise StoreCorruptError(f"Invalid JSON in store file: {path}") from exc
        if not isinstance(value, dict):
            raise StoreCorruptError(f"Store file must contain an object: {path}")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise StoreCorruptError(f"Unsupported schema in store file: {path}")
        return value

    def _read_regular_text(
        self, path: Path, *, max_bytes: int = MAX_STORE_FILE_BYTES
    ) -> str:
        self._reject_symlink(path)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise InvalidDataPathError(f"Cannot safely open store file: {path}") from exc
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise InvalidDataPathError(f"Store entry is not a regular file: {path}")
            if details.st_size > max_bytes:
                raise StoreCorruptError(f"Store file exceeds the size limit: {path}")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                raw = stream.read(max_bytes + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > max_bytes:
            raise StoreCorruptError(f"Store file exceeds the size limit: {path}")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StoreCorruptError(f"Store file is not valid UTF-8: {path}") from exc

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._atomic_write(path, content)

    def _atomic_write(self, path: Path, content: str) -> None:
        self._secure_mkdir(path.parent)
        self._reject_symlink(path, allow_missing=True)
        temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(6)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    @contextmanager
    def _store_lock(self) -> Iterator[None]:
        """Hold the persistent store-wide advisory lock for one transaction."""
        current_pid = os.getpid()
        if current_pid != self._lock_pid:
            # A lock inherited from a multi-threaded parent can remain permanently
            # owned by a thread that does not exist in the forked child.
            self._thread_lock = threading.RLock()
            self._lock_state = threading.local()
            self._lock_pid = current_pid
        with self._thread_lock:
            depth = getattr(self._lock_state, "depth", 0)
            if depth:
                self._lock_state.depth = depth + 1
                try:
                    yield
                finally:
                    self._lock_state.depth -= 1
                return

            self._secure_mkdir(self.root)
            lock_path = self.root / ".store.lock"
            self._reject_symlink(lock_path, allow_missing=True)
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = _open_registered_lock_descriptor(lock_path, flags)
            except OSError as exc:
                raise InvalidDataPathError(
                    f"Cannot safely open store lock: {lock_path}"
                ) from exc
            try:
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                    raise InvalidDataPathError(
                        f"Store lock is not a private regular file: {lock_path}"
                    )
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                current = os.stat(lock_path, follow_symlinks=False)
                if (
                    not stat.S_ISREG(current.st_mode)
                    or current.st_dev != details.st_dev
                    or current.st_ino != details.st_ino
                ):
                    raise InvalidDataPathError(
                        f"Store lock changed while it was being opened: {lock_path}"
                    )
                self._lock_state.depth = 1
                try:
                    yield
                finally:
                    self._lock_state.depth = 0
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                _close_registered_lock_descriptor(descriptor)

    def _secure_mkdir(self, path: Path) -> None:
        self._reject_symlink(path, allow_missing=True)
        if path == self.root:
            if path.exists() or path.is_symlink():
                self._reject_symlink(path)
                if not path.is_dir():
                    raise InvalidDataPathError(
                        f"Storage path is not a directory: {path}"
                    )
            else:
                try:
                    path.mkdir(parents=True, mode=0o700)
                except FileExistsError:
                    # Another process may have bootstrapped the store concurrently.
                    pass
                self._reject_symlink(path)
                if not path.is_dir():
                    raise InvalidDataPathError(
                        f"Storage path is not a directory: {path}"
                    )
            path.chmod(0o700)
            return

        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise InvalidDataPathError(
                f"Storage child escapes the data directory: {path}"
            ) from exc

        self._secure_mkdir(self.root)
        current = self.root
        for component in relative.parts:
            current = current / component
            if current.exists() or current.is_symlink():
                self._reject_symlink(current)
                if not current.is_dir():
                    raise InvalidDataPathError(
                        f"Storage path is not a directory: {current}"
                    )
            else:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    # A concurrent holder can create the same safe component.
                    pass
            self._reject_symlink(current)
            current.chmod(0o700)

    @staticmethod
    def _reject_symlink(path: Path, *, allow_missing: bool = False) -> None:
        absolute = path.absolute()
        if ".." in absolute.parts:
            raise InvalidDataPathError(f"Parent traversal is not allowed: {path}")
        # lstat on the leaf alone follows symlinks in every parent directory.
        for component in (*reversed(absolute.parents), absolute):
            try:
                mode = component.lstat().st_mode
            except FileNotFoundError:
                if allow_missing:
                    return
                raise
            if stat.S_ISLNK(mode):
                raise InvalidDataPathError(f"Symbolic links are not allowed: {component}")
