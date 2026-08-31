# Agent catalog TDD evidence

## Source and user journey

The journey was derived from the approved Gate 1 plan: an OmaRecall user can
choose the effective Omarchy agent menu entries, or follow the current Omarchy
default, without weakening context-file or process-launch safety.

## RED and GREEN

The focused command was:

```text
PYTHONPATH=src python3 -m unittest tests.test_launcher tests.test_plugin_contract -v
```

Before implementation it ran 15 tests and failed as intended: six new explicit
agents were rejected, the known-default test used the fallback, the fallback
lacked `--inline`, OpenCode had the old argument shape, and the selector was
absent. After implementation the same 15 tests passed.

A review follow-up added a launch-adjacent safety warning. A pre-Gate-2 catalog
revision also added a regression check that the locally deprecated agent stays
absent and rejected whether selected explicitly or returned by the default
resolver. The focused suite failed on the stale catalog locations and then on
the default fallback path before passing after implementation.

The full command was:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

All 84 tests passed. `compileall`, QML formatting checks, `git diff --check`,
and `omarchy plugin validate .` also passed.

## Test specification

| Guarantee | Test | Type | Result |
|---|---|---|---|
| All nine explicit adapters use the expected argument shape and include the checkpoint bootstrap | `test_supported_adapters_build_interactive_commands` | Unit | PASS |
| A known Omarchy default uses its direct adapter | `test_known_default_agent_uses_direct_adapter` | Unit | PASS |
| An unknown future default uses `omarchy-agent --inline --prompt` | `test_unknown_default_agent_uses_omarchy_launcher_fallback` | Unit | PASS |
| An unknown explicit agent is rejected | `test_unknown_explicit_agent_is_rejected` | Unit | PASS |
| A deprecated default agent is rejected before session creation instead of reaching the future-agent fallback | `test_deprecated_gemini_default_is_rejected_before_session_creation` | Unit | PASS |
| Recalled memory stays in its private context file rather than argv | `test_prepare_keeps_memory_out_of_process_arguments` | Integration | PASS |
| The panel exposes all choices in a compact accessible selector and omits `--agent` for the default sentinel | `test_panel_offers_effective_omarchy_agents_in_accessible_selector` | Contract | PASS |
| The preview visibly warns about unattended approval and untrusted recalled memory beside the launch action | `test_preview_warns_before_unattended_agent_launch` | Contract | PASS |

## Coverage and gaps

The repository does not provide a coverage command or configured coverage
tool, so the complete 84-test suite is recorded instead of a numeric coverage
percentage. Agent binaries are mocked; compatibility is locked to the active
local Omarchy launcher argument contract rather than exercising cloud agents.
No TDD checkpoint commits were created because the approved task explicitly
requires review before commit.
