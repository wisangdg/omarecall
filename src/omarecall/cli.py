"""JSON command-line interface used by humans and the future QML panel."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from omarecall.context_builder import ContextBuilder
from omarecall.errors import InvalidSessionError, OmaRecallError
from omarecall.importer import ConversationImporter
from omarecall.launcher import AgentLauncher, LaunchRequest
from omarecall.store import SessionMetadata, SessionStore


def _metadata_payload(metadata: SessionMetadata) -> dict[str, Any]:
    return metadata.to_dict()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omarecall")
    parser.add_argument("--data-dir", type=Path, help="Override the XDG data directory")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize the local store")
    commands.add_parser("reindex", help="Rebuild the session index")

    session = commands.add_parser("session", help="Create and inspect sessions")
    session_commands = session.add_subparsers(dest="session_command", required=True)

    create = session_commands.add_parser("create")
    create.add_argument("--project", type=Path, default=Path.cwd())
    create.add_argument("--agent", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--title")

    list_sessions = session_commands.add_parser("list")
    list_sessions.add_argument("--project-id")
    list_sessions.add_argument(
        "--status", choices=("active", "completed", "interrupted", "archived")
    )
    list_sessions.add_argument("--limit", type=int, default=100)

    show = session_commands.add_parser("show")
    show.add_argument("session_id")
    show.add_argument("--max-note-chars", type=int)
    show.add_argument("--max-import-chars", type=int)

    checkpoint = commands.add_parser("checkpoint", help="Merge a session checkpoint")
    checkpoint.add_argument("session_id")
    checkpoint.add_argument("--completed", action="append", default=[])
    checkpoint.add_argument("--decision", action="append", default=[])
    checkpoint.add_argument("--pending", action="append", default=[])
    checkpoint.add_argument(
        "--resolve-pending", action="append", default=[], metavar="TEXT",
        help="Move an exact pending item to Completed (repeatable)",
    )
    checkpoint.add_argument(
        "--remove-pending", action="append", default=[], metavar="TEXT",
        help="Remove an exact pending item without marking it completed (repeatable)",
    )
    checkpoint.add_argument("--file", action="append", default=[])
    checkpoint.add_argument("--warning", action="append", default=[])
    checkpoint.add_argument(
        "--status", choices=("active", "completed", "interrupted", "archived")
    )

    archive = commands.add_parser("archive", help="Archive a session")
    archive.add_argument("session_id")

    pin = commands.add_parser("pin", help="Pin or unpin a session")
    pin.add_argument("session_id")
    pin.add_argument("--value", required=True, choices=("true", "false"))

    delete = commands.add_parser("delete", help="Permanently delete one session")
    delete.add_argument("session_id")
    delete.add_argument("--confirm", required=True)

    import_conversation = commands.add_parser(
        "import", help="Import one external AI conversation"
    )
    import_conversation.add_argument("--file", type=Path, required=True)
    import_conversation.add_argument("--project", type=Path, default=Path.cwd())
    import_conversation.add_argument("--title")

    context = commands.add_parser("context", help="Build a previewable memory packet")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    build = context_commands.add_parser("build")
    build.add_argument("--mode", required=True, choices=("clean", "session", "relevant"))
    build.add_argument("--session-id")
    project = build.add_mutually_exclusive_group()
    project.add_argument("--project-id")
    project.add_argument("--project", type=Path, help="Resolve context from a project directory")
    build.add_argument("--max-tokens", type=int, default=8_000)

    launch = commands.add_parser("launch", help="Start an agent with selected memory")
    launch.add_argument("--project", type=Path, default=Path.cwd())
    launch.add_argument("--agent")
    launch.add_argument("--goal", required=True)
    launch.add_argument("--title")
    launch.add_argument("--mode", required=True, choices=("clean", "session", "relevant"))
    launch.add_argument("--session-id")
    launch.add_argument("--max-tokens", type=int, default=8_000)
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument("--expect-context")

    run_agent = commands.add_parser(
        "run-agent", help="Run an agent in the current terminal and finalize its session"
    )
    run_agent.add_argument("session_id")
    run_agent.add_argument("agent_argv", nargs=argparse.REMAINDER)
    return parser


def _agent_signals() -> None:
    """Let Ctrl-C reach the agent while still finalizing the session on shutdown."""
    try:
        signal.signal(signal.SIGINT, lambda signum, frame: None)
    except (AttributeError, ValueError, OSError):
        pass

    def _terminate(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for name in ("SIGHUP", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _terminate)
        except (ValueError, OSError):
            pass


def _run_agent(store: SessionStore, session_id: str, argv: Sequence[str]) -> int:
    command = list(argv)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise InvalidSessionError("run-agent requires an agent command")
    _agent_signals()
    exit_code = 0
    try:
        try:
            exit_code = subprocess.run(command, check=False).returncode
        except FileNotFoundError:
            exit_code = 127
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        try:
            store.mark_interrupted_if_active(session_id)
        except OmaRecallError:
            pass
    return exit_code


def _dispatch(args: argparse.Namespace, store: SessionStore) -> dict[str, Any]:
    if args.command == "import":
        imported = ConversationImporter().load(args.file)
        metadata = store.create_imported_session(
            project_path=args.project,
            title=args.title or imported.title,
            transcript=imported.transcript,
            source_name=imported.source_name,
            source_format=imported.source_format,
        )
        return {
            "ok": True,
            "session": _metadata_payload(metadata),
            "import": {
                "source_name": imported.source_name,
                "format": imported.source_format,
                "message_count": imported.message_count,
                "warnings": list(imported.warnings),
            },
        }
    if args.command == "launch":
        request = LaunchRequest(
            project_path=args.project,
            agent=args.agent,
            goal=args.goal,
            title=args.title,
            mode=args.mode,
            source_session_id=args.session_id,
            max_tokens=args.max_tokens,
            expected_context_fingerprint=args.expect_context,
        )
        launcher = AgentLauncher(store)
        plan = launcher.prepare(request) if args.dry_run else launcher.launch(request)
        return {"ok": True, "launch": plan.to_dict()}
    if args.command == "context" and args.context_command == "build":
        project_id = args.project_id
        if args.project is not None:
            project_id, _, _ = store.project_identity(args.project)
        context = ContextBuilder(store).build(
            mode=args.mode,
            session_id=args.session_id,
            project_id=project_id,
            max_tokens=args.max_tokens,
        )
        return {"ok": True, "context": context.to_dict()}
    if args.command == "init":
        store.initialize()
        return {"ok": True, "data_dir": str(store.root)}
    if args.command == "reindex":
        return {"ok": True, "session_count": store.reindex()}
    if args.command == "archive":
        metadata = store.archive_session(args.session_id)
        return {"ok": True, "session": _metadata_payload(metadata)}
    if args.command == "pin":
        metadata = store.set_pinned(args.session_id, args.value == "true")
        return {"ok": True, "session": _metadata_payload(metadata)}
    if args.command == "delete":
        if args.confirm != args.session_id:
            raise InvalidSessionError("Delete confirmation must match the session ID")
        store.delete_session(args.session_id)
        return {"ok": True, "deleted_session_id": args.session_id}
    if args.command == "checkpoint":
        metadata = store.add_checkpoint(
            args.session_id,
            completed=args.completed,
            decisions=args.decision,
            pending=args.pending,
            resolve_pending=args.resolve_pending,
            remove_pending=args.remove_pending,
            files=args.file,
            warnings=args.warning,
            status=args.status,
        )
        return {"ok": True, "session": _metadata_payload(metadata)}
    if args.command == "session" and args.session_command == "create":
        metadata = store.create_session(
            project_path=args.project,
            agent=args.agent,
            goal=args.goal,
            title=args.title,
        )
        return {"ok": True, "session": _metadata_payload(metadata)}
    if args.command == "session" and args.session_command == "list":
        sessions = store.list_sessions(
            project_id=args.project_id,
            status=args.status,
            limit=args.limit,
        )
        return {
            "ok": True,
            "sessions": [_metadata_payload(metadata) for metadata in sessions],
        }
    if args.command == "session" and args.session_command == "show":
        session = store.get_session(args.session_id)
        if args.max_note_chars is not None:
            if args.max_note_chars < 1:
                raise InvalidSessionError("max-note-chars must be positive")
            note = str(session["note"])
            if len(note) > args.max_note_chars:
                session["note"] = note[: args.max_note_chars] + "\n\n[NOTE TRUNCATED]"
        if args.max_import_chars is not None and args.max_import_chars < 1:
            raise InvalidSessionError("max-import-chars must be positive")
        imported_conversation = store.get_imported_conversation(args.session_id)
        if imported_conversation is not None:
            if (
                args.max_import_chars is not None
                and len(imported_conversation) > args.max_import_chars
            ):
                imported_conversation = (
                    imported_conversation[: args.max_import_chars]
                    + "\n\n[IMPORT TRUNCATED]"
                )
            session["imported_conversation"] = imported_conversation
        return {"ok": True, "session": session}
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = SessionStore(args.data_dir) if args.data_dir else SessionStore.default()
    try:
        if args.command == "run-agent":
            return _run_agent(store, args.session_id, args.agent_argv)
        payload = _dispatch(args, store)
    except OmaRecallError as exc:
        json.dump(
            {"ok": False, "error": {"code": exc.code, "message": str(exc)}},
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        json.dump(
            {
                "ok": False,
                "error": {"code": "filesystem_error", "message": str(exc)},
            },
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
