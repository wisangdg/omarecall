from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import stat
import threading
import warnings
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import omarecall.store as store_module
from omarecall.errors import InvalidDataPathError, InvalidSessionError
from omarecall.store import SessionStore

PROCESS_TIMEOUT = 10


class _GatedIndexStore(SessionStore):
    """Pause the actual index upsert after it has read the old index."""

    def __init__(  # type: ignore[no-untyped-def]
        self, root: Path, label: str, arrivals, release
    ) -> None:
        super().__init__(root)
        self._label = label
        self._arrivals = arrivals
        self._release = release
        self._index_reads = 0

    def _read_json(self, path: Path):  # type: ignore[no-untyped-def]
        value = super()._read_json(path)
        if path == self.index_path:
            self._index_reads += 1
            if self._index_reads == 2:
                self._arrivals.put(self._label)
                if not self._release.wait(PROCESS_TIMEOUT):
                    raise TimeoutError("test did not release gated index writer")
        return value


class _GatedMutationStore(SessionStore):
    """Pause after loading stale session state for a read-modify-write."""

    def __init__(  # type: ignore[no-untyped-def]
        self, root: Path, label: str, arrivals, release
    ) -> None:
        super().__init__(root)
        self._label = label
        self._arrivals = arrivals
        self._release = release
        self._waited = False

    def _find_session(self, session_id: str):  # type: ignore[no-untyped-def]
        result = super()._find_session(session_id)
        if not self._waited:
            self._waited = True
            self._arrivals.put(self._label)
            if not self._release.wait(PROCESS_TIMEOUT):
                raise TimeoutError("test did not release gated session writer")
        return result


def _create_gated_session(
    root: str, project: str, session_id: str, arrivals, release
) -> None:  # type: ignore[no-untyped-def]
    _GatedIndexStore(Path(root), session_id, arrivals, release).create_session(
        project_path=project,
        agent="codex",
        goal=f"Create {session_id}",
        session_id=session_id,
        now=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )


def _mutate_gated_session(
    root: str, session_id: str, operation: str, arrivals, release
) -> None:  # type: ignore[no-untyped-def]
    store = _GatedMutationStore(Path(root), operation, arrivals, release)
    if operation == "checkpoint":
        store.add_checkpoint(session_id, completed=["checkpoint survived"])
    else:
        store.set_pinned(session_id, True)


def _use_inherited_store(  # type: ignore[no-untyped-def]
    store: SessionStore, inherited_descriptor: int, lock_identity, proceed, results
) -> None:
    try:
        details = os.fstat(inherited_descriptor)
    except OSError:
        descriptor_inherited = False
    else:
        descriptor_inherited = (details.st_dev, details.st_ino) == lock_identity
    results.put(("descriptor_inherited", descriptor_inherited))
    if not proceed.wait(PROCESS_TIMEOUT):
        raise TimeoutError("test did not release forked child")
    results.put(("sessions", len(store.list_sessions())))


def _lock_is_held(lock_path: Path) -> bool:
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _stop_processes(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        if process.pid is not None and process.is_alive():
            process.terminate()
    for process in processes:
        if process.pid is not None:
            process.join(timeout=PROCESS_TIMEOUT)


class SessionStoreConcurrencyTests(TestCase):
    def _run_gated_pair(
        self, root: Path, processes, arrivals, releases
    ) -> None:  # type: ignore[no-untyped-def]
        first, second = processes
        try:
            first.start()
            self.assertEqual(first.name, arrivals.get(timeout=PROCESS_TIMEOUT))
            lock_held = _lock_is_held(root / ".store.lock")
            second.start()

            if lock_held:
                releases[0].set()
                first.join(timeout=PROCESS_TIMEOUT)
                self.assertEqual(0, first.exitcode)
                self.assertEqual(second.name, arrivals.get(timeout=PROCESS_TIMEOUT))
            else:
                # Without a transaction lock, both children deterministically hold
                # stale state before either writer is released.
                self.assertEqual(second.name, arrivals.get(timeout=PROCESS_TIMEOUT))
                releases[0].set()
                first.join(timeout=PROCESS_TIMEOUT)
                self.assertEqual(0, first.exitcode)

            releases[1].set()
            second.join(timeout=PROCESS_TIMEOUT)
            self.assertEqual(0, second.exitcode)
        finally:
            for release in releases:
                release.set()
            _stop_processes(processes)

    def test_concurrent_creates_do_not_lose_an_index_update(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "data"
            project = base / "project"
            project.mkdir()
            SessionStore(root).initialize()
            context = multiprocessing.get_context("fork")
            arrivals = context.Queue()
            releases = [context.Event(), context.Event()]
            processes = [
                context.Process(
                    name=session_id,
                    target=_create_gated_session,
                    args=(str(root), str(project), session_id, arrivals, release),
                )
                for session_id, release in zip(
                    ("concurrent-one", "concurrent-two"), releases, strict=True
                )
            ]

            self._run_gated_pair(root, processes, arrivals, releases)

            sessions = json.loads((root / "index.json").read_text())["sessions"]
            self.assertEqual(
                {"concurrent-one", "concurrent-two"},
                {session["id"] for session in sessions},
            )

    def test_concurrent_checkpoint_and_pin_preserve_both_changes(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "data"
            project = base / "project"
            project.mkdir()
            store = SessionStore(root)
            session = store.create_session(
                project_path=project,
                agent="codex",
                goal="Preserve concurrent changes",
                session_id="shared-session",
            )
            context = multiprocessing.get_context("fork")
            arrivals = context.Queue()
            releases = [context.Event(), context.Event()]
            operations = ("pin", "checkpoint")
            processes = [
                context.Process(
                    name=operation,
                    target=_mutate_gated_session,
                    args=(str(root), session.id, operation, arrivals, release),
                )
                for operation, release in zip(operations, releases, strict=True)
            ]

            self._run_gated_pair(root, processes, arrivals, releases)

            stored = store.get_session(session.id)
            self.assertTrue(stored["pinned"])
            self.assertIn("- checkpoint survived", stored["note"])

    def test_inherited_store_lock_is_closed_and_state_reset_after_fork(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            store = SessionStore(root)
            store.initialize()
            flock_acquired = threading.Event()
            release_thread = threading.Event()
            held_descriptors: list[int] = []

            def hold_store_lock() -> None:
                with store._store_lock():
                    held_descriptors.append(
                        next(iter(store_module._ACTIVE_LOCK_DESCRIPTORS))
                    )
                    flock_acquired.set()
                    release_thread.wait(PROCESS_TIMEOUT)

            holder = threading.Thread(target=hold_store_lock)
            holder.start()
            self.assertTrue(flock_acquired.wait(PROCESS_TIMEOUT))
            context = multiprocessing.get_context("fork")
            results = context.Queue()
            proceed = context.Event()
            process = context.Process(
                target=_use_inherited_store,
                args=(
                    store,
                    held_descriptors[0],
                    (
                        (root / ".store.lock").stat().st_dev,
                        (root / ".store.lock").stat().st_ino,
                    ),
                    proceed,
                    results,
                ),
            )
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    process.start()
                self.assertEqual(
                    ("descriptor_inherited", False),
                    results.get(timeout=PROCESS_TIMEOUT),
                )
                self.assertTrue(_lock_is_held(root / ".store.lock"))
                release_thread.set()
                holder.join(timeout=PROCESS_TIMEOUT)
                self.assertFalse(holder.is_alive())
                proceed.set()
                self.assertEqual(
                    ("sessions", 0), results.get(timeout=PROCESS_TIMEOUT)
                )
                process.join(timeout=PROCESS_TIMEOUT)
                self.assertEqual(0, process.exitcode)
            finally:
                release_thread.set()
                proceed.set()
                holder.join(timeout=PROCESS_TIMEOUT)
                _stop_processes([process])

    def test_lock_file_is_private_regular_and_persistent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            store = SessionStore(root)
            store.initialize()

            lock_path = root / ".store.lock"
            self.assertTrue(stat.S_ISREG(lock_path.stat().st_mode))
            self.assertEqual(0o600, lock_path.stat().st_mode & 0o777)
            store.list_sessions()
            self.assertTrue(lock_path.exists())

    def test_symlink_lock_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "data"
            root.mkdir(mode=0o700)
            target = base / "outside"
            target.write_text("do not lock")
            (root / ".store.lock").symlink_to(target)

            with self.assertRaises(InvalidDataPathError):
                SessionStore(root).initialize()

            self.assertEqual("do not lock", target.read_text())

    def test_hard_link_lock_file_is_rejected_without_changing_target_mode(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "data"
            root.mkdir(mode=0o700)
            target = base / "outside"
            target.write_text("do not lock")
            target.chmod(0o644)
            (root / ".store.lock").hardlink_to(target)

            with self.assertRaises(InvalidDataPathError):
                SessionStore(root).initialize()

            self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_lock_is_released_after_an_operation_raises(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            store = SessionStore(root)

            with self.assertRaises(InvalidSessionError):
                store.list_sessions(status="not-a-status")

            self.assertEqual([], store.list_sessions())
