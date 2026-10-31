# Phase 94 — evidence-backed specialist orchestration capability

## Summary

Update the canonical capability manifest so specialist orchestration reflects the code and tests that exist today. It is classified as **experimental**, not planned and not production-ready.

## Verified implementation surface

- `automation/agent_coordinator.py`: deterministic capability routing, bounded plans, dependencies, authorization and durable plan records.
- `automation/specialist_worker.py`: provider-backed text-only handlers, task claims, leases, heartbeats, recovery and bounded result persistence.
- `automation/task_runtime.py` and `automation/workflow_store.py`: durable task lifecycle, optimistic versioning and event history.
- `automation/authorized_read_tools.py` and `automation/specialist_read_tool_adapter.py`: permission-checked read-only workspace evidence for explicitly configured handlers.
- `automation/approved_workspace_changes.py` and `automation/engineering_workflow.py`: opt-in digest-bound file edits and allow-listed test execution.

## Limits that remain

- Specialist actions are limited to registered `emit` handlers; no arbitrary model-generated tool calls or code execution.
- Worker handlers execute in the Yuki process. A heartbeat renews leases but does not terminate a hung or malicious handler.
- The approved test runner is not a sandbox; it executes trusted test modules with the Yuki process permissions.
- The engineering workflow is opt-in and requires application wiring for trusted identity, approval, audit, and allow-lists.
- Hosted multi-tenant orchestration, production scheduler, hardened per-task isolation, complete desktop integration, and production observability are not established by these modules.
- This capability is not a verified 100-agent production service.

## Validation

The capability-manifest test asserts the experimental status, runtime entrypoints, named regression evidence and explicit limitations. The repository's capability manifest validator also checks that declared source and test evidence exists. Merge only after exact-head CI and full quality gates pass.
