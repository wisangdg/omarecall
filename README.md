# OmaRecall

OmaRecall is a local, inspectable memory layer for AI agent sessions on
Omarchy. It keeps structured session notes, lets you review exactly which
memory will be recalled, and starts a new interactive agent with that context.

## Features

- Scrollable session history in an Omarchy-native bar panel.
- Start with relevant project context, one selected session, or no memory.
- Import one external `.md`, `.txt`, or supported `.json` conversation as a
  completed session attached to a chosen project.
- Exact context preview with a fingerprint that prevents launch if the source
  changes after review.
- Interactive adapters for Codex, Claude, and OpenCode, plus an
  `omarchy-agent` fallback for future default agents.
- Agent checkpoint contract for goals, completed work, decisions, pending work,
  relevant files, and warnings.
- Pin, complete, archive, and explicitly confirmed delete actions.
- Local Markdown notes and rebuildable JSON indexes; no database or network.
- Secret redaction, private file permissions, atomic writes, and symlink/path
  traversal protection.
- Keyboard navigation and accessible labels throughout the panel.

## Install

From a published Git repository:

```bash
omarchy plugin add https://github.com/OWNER/omarecall.git --enable
```

For local development, commit the checkout and pass its absolute path to
`omarchy plugin add`.

Open the panel from its bar icon or with:

```bash
omarchy-shell shell toggle wdg.omarecall
```

## How launch works

1. Select a previous session, or enter a project path for a clean start.
2. Enter the goal and choose Codex, Claude, or OpenCode.
3. Review relevant project memory, one session, or a clean-session notice.
4. Start the agent. OmaRecall creates a new session and gives the agent a
   checkpoint command.

The project field starts at the current user's home directory. Set
`OMARECALL_DEFAULT_PROJECT` before starting Omarchy Shell to use a different
portable default; a selected session's project path takes precedence.

The recalled packet is stored as a private file beside the new session. Only a
short instruction and that file path appear in the agent process arguments;
the recalled memory itself is not placed in the process list.

## Importing an external conversation

Choose the project that the conversation belongs to, then select **Import
conversation…**. Each file becomes one completed session and can immediately
be previewed with **This session** or included through **Relevant project**.

Markdown and plain text are the dependable interchange formats. JSON import is
best-effort for one generic `messages` conversation, a ChatGPT `mapping`
conversation, or Claude `chat_messages`. A full export containing multiple
conversations is rejected instead of silently mixing unrelated chats into one
project. Files must be UTF-8 regular files, cannot be symlinks, and are limited
to 2 MiB.

## Storage

Session data defaults to:

```text
${XDG_DATA_HOME:-~/.local/share}/omarecall/
├── projects.json
├── index.json
└── projects/<project-id>/sessions/<session-id>/
    ├── meta.json
    ├── note.md
    ├── import.md   # only for explicitly imported conversations
    └── context.md  # only for launches with recalled memory
```

Directories use mode `0700` and files use `0600`. `index.json` is a cache and
can be recreated with `omarecall reindex`. Uninstalling the plugin never removes
session data automatically.

Raw transcripts are stored only when the user explicitly imports them. OmaRecall
does not automatically capture sessions started elsewhere.

## CLI

```bash
./bin/omarecall session list --limit 100
./bin/omarecall session show SESSION_ID
./bin/omarecall checkpoint SESSION_ID --completed "Implemented storage"
./bin/omarecall import --file conversation.md --project "$PWD"
./bin/omarecall context build --mode session --session-id SESSION_ID
./bin/omarecall launch --agent codex --project "$PWD" \
  --goal "Continue the work" --mode session --session-id SESSION_ID
```

Every successful command prints JSON to stdout. Expected errors print a stable
JSON error object to stderr and exit with status 2.

## Development

The runtime uses only the Python standard library.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
omarchy plugin validate .
qmlformat -n BarWidget.qml >/dev/null
qmlformat -n Panel.qml >/dev/null
```
