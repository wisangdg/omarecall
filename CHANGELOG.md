# Changelog

## Unreleased

- Expand the launcher catalog to Claude, Codex, Copilot, Crush, Grok, Oh My
  Pi, OpenCode, Pi, and Antigravity using their Omarchy interactive flags.
- Add an accessible compact agent selector with an **Omarchy default** option.
- Show an adjacent warning before launch that agents use unattended approval
  modes and recalled memory remains untrusted.
- Polish the launch form with the themed Omarchy dropdown, clearer memory
  choices, stronger session-action hierarchy, and duplicate goal-prefix
  prevention.
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
