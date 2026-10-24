# Phase 122 — Guarded CLI tool execution

This phase migrates the existing `/tool` CLI path from direct `run_tool()`
execution to the shared `invoke_registered_skill()` authorization boundary.

## Behavior

- Existing `yuki_tools.TOOLS` remains the source allowlist; arbitrary names are not imported or discovered.
- `/tool NAME ARGUMENT` passes through the local security policy and guarded runtime.
- `/tool approve NAME` grants one process-local invocation for approval-gated tools.
- The current calculator, time, and workspace file listing are marked low risk.
- `save_note` requires explicit approval because it writes to disk.
- One-shot approval is consumed before execution, including when execution fails.
- Audit events contain action/status metadata only.

## Examples

```text
/tool calculate 12 * (3 + 4)
/tool save_note a note
/tool approve save_note
/tool save_note a note
```

The first `save_note` invocation is blocked until approval. Approval does not
sandbox a tool; it runs with Yuki's process permissions.

## Verification

```bash
python -m unittest tests.test_guarded_tool_adapter -v
python -m unittest tests.test_guarded_tool_cli -v
python -m unittest discover -s tests -v
```

## Scope

This migrates the CLI's existing tool path. It does not prove every task planner,
worker, or external plugin path is guarded. Those paths require separate call-graph
audits and integration tests before claiming a single enforced execution boundary.
