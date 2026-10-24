# Phase 129 — Guarded tool dispatch

Routes the `/tool NAME [argument]` CLI path through `GuardedToolRuntime`.

## Guarantees

- Validates tool name and bounds argument size before dispatch.
- Requires the existing `SecurityPolicy.authorize_tool` decision to allow the tool; policy errors fail closed.
- Calls only entries from the explicit `TOOLS` registry.
- Converts tool exceptions into a generic failure response.
- Bounds returned text and emits metadata-only audit events.
- Does not log arguments, results, or exception contents.

## Limits

This wrapper is not a process sandbox. The registered functions still run with Yuki's Python process privileges and must be individually least-privileged. It guards the CLI `/tool` dispatch path; it does not prove all internal Python call sites, plugins, planners, or workers use this boundary.

## Verification

    python -m unittest tests.test_guarded_tool_runtime -v
    python -m unittest discover -s tests -v

Draft pending CI and review. This phase is stacked on Phase 128, which is stacked on Phase 127; preserve that dependency order when merging.