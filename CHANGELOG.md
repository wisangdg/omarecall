# Changelog

## 0.4.1 — 2026-09-12

- Finalize each launched session when its agent exits. Sessions that never
  receive a final `completed` checkpoint are now marked `interrupted` instead
  of staying `active` forever, so the bar badge reflects real running agents.

## 0.4.0 — 2026-09-08

- Resolve or remove individual pending tasks through repeatable checkpoint flags.
- Preserve panel selection by session ID and select newly imported or launched sessions.
- Include the selected data directory in agent checkpoint instructions.
- Reject symlink ancestors before accessing or creating store entries.
- Preserve multiline checkpoint content using a backward-readable note parser
  and a versioned Markdown format for new writes.
- Build project previews from the entered project directory, matching launch.
- Reserve space for truncation markers within the context character budget.
- Preserve multiline notes when files use Windows (CRLF) line endings.

## 0.3.0 — 2026-08-31

- Expand the launcher catalog to Claude, Codex, Copilot, Crush, Grok, Oh My
  Pi, OpenCode, Pi, and Antigravity using their Omarchy interactive flags.
- Add an accessible compact agent selector with an **Omarchy default** option.
- Show an adjacent warning before launch that agents use unattended approval
  modes and recalled memory remains untrusted.
- Polish the launch form with the themed Omarchy dropdown, clearer memory
  choices, stronger session-action hierarchy, and duplicate goal-prefix
  prevention, plus a compact accessible close action.
- Delegate an unknown future default through `omarchy-agent --inline --prompt`
  while continuing to reject unknown explicit agent names.

## 0.2.0 — 2026-08-29

- Import external Markdown, text, generic JSON, ChatGPT, and Claude
  conversations as project-scoped completed sessions.
- Store imported transcripts privately and recall them through the existing
  preview, redaction, token-budget, and fingerprint pipeline.
- Add a native, accessible conversation file picker to the panel.

## 0.1.0 — 2026-08-29

- Initial local-first session store and JSON CLI.
- Context preview, budgeting, provenance, redaction, and fingerprints.
- Codex, Claude, and OpenCode launch adapters.
- Omarchy bar widget and accessible history panel.
- Pin, complete, archive, and confirmed deletion controls.
