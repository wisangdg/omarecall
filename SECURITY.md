# Security model

OmaRecall runs locally with the user's permissions, like every Omarchy shell
plugin. Its memory files may contain sensitive project information even after
automatic redaction, so the local data directory should not be shared publicly.

## Protections

- No network access or cloud account is required.
- Raw transcripts are stored only after an explicit import and remain in a
  private `import.md` file with mode `0600`.
- Import accepts only bounded UTF-8 regular files and rejects symlinks,
  non-regular files, binary/NUL content, malformed input, and unsupported
  multi-conversation exports.
- Common API keys, bearer tokens, credential URLs, and private-key blocks are
  redacted before recalled context is created.
- Storage directories and files are private to the user.
- Writes use a same-directory temporary file, `fsync`, and atomic replacement.
- A private persistent lock serializes complete store transactions across
  concurrent OmaRecall processes and prevents read-modify-write lost updates.
- Symlink storage targets, nested symlink paths, and traversal-shaped session
  IDs are rejected.
- Session deletion only removes known regular files and refuses unknown entries.
- Context is passed to agents by private file path, not embedded in process
  arguments.
- Agent choices map to a fixed internal adapter catalog; OmaRecall does not
  execute or interpret shell actions from Omarchy menu configuration.
- Unknown explicit agent names are rejected. Only an unknown agent returned by
  the user's Omarchy default resolver is delegated to `omarchy-agent --inline`
  with the bootstrap prompt as one argument.
- Deprecated agent identities are denied before fallback or session creation,
  so an obsolete Omarchy default cannot bypass the explicit catalog.
- A preview fingerprint prevents silently launching with context that changed
  after the user reviewed it.
- Recalled text is explicitly marked as untrusted historical data to reduce
  persistent prompt-injection risk.

## Limitations

Pattern redaction cannot guarantee discovery of every secret. Always inspect
the preview before launch. Imported conversations may contain stale facts,
secrets, or prompt-injection text; their raw private copy is not rewritten by
redaction, while recalled context is redacted and marked untrusted. Any enabled
Omarchy plugin is unsandboxed code and
can access data available to the user account; review third-party plugin source
before enabling it.

The interactive adapters use the same unattended/auto-approval modes as the
Omarchy agent launcher. These modes grant the selected agent broad authority in
the user's session; review recalled context and the agent's own security model
before starting it. OmaRecall does not install or authenticate agent CLIs.

To report a vulnerability, open a private security advisory in the repository
instead of posting sensitive reproduction data in a public issue.
