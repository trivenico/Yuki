# Phase 85 — specialist handler worker

## Scope

Phase 85 adds a bounded execution bridge between Yuki's durable task queue and explicit specialist handlers.

- Four provider-backed text-only roles: research, coding, testing, reporting.
- Worker claims tasks through the durable store; approval-required tasks remain unclaimable until the store records valid approval.
- Worker executes only a handler explicitly registered for the coordinator's specialist ID.
- Task completion/failure uses the existing runtime, lease ownership, version checks, and task event journal.
- Handler errors are redacted to stable error codes.
- Output is bounded and persisted as a text artifact.

## Safety boundaries

The handlers do not execute generated code, invoke a shell, alter the workspace, or perform implicit network access. The research role only analyzes supplied material and must not fabricate citations or imply it browsed. The coding role creates proposed changes only. The testing role produces a test plan and does not claim to run tests. Reporting distinguishes supplied evidence from assumptions.

Provider access is injected explicitly; provider availability is checked at execution time. No provider key or prompt text is written to logs by this module. The worker must be configured with a service identity authorized for task read/complete/fail. User-facing API code must not accept the service actor identity from an untrusted client.

## Integration

1. Construct the existing `WorkflowRunStore` and `TaskRuntime` with the application's trusted authorization callback.
2. Obtain an approved provider adapter from Yuki's provider manager.
3. Call `build_provider_specialist_handlers(provider)`.
4. Register only matching specialist IDs in `SpecialistTaskWorker`.
5. Run `run_once()` from an explicitly managed worker process. This module does not create a scheduler or background process automatically.

The coordinator's registered action remains the existing allow-listed `emit` action. The worker dispatches by the stable coordinator task-name prefix and registered specialist ID; it does not accept arbitrary handler names from task arguments.

## Validation

The dedicated Phase 85 GitHub Actions workflow runs compilation, worker tests, coordinator tests, task-runtime tests, workflow-store tests, workflow-engine safety tests, and dependency consistency checks on Python 3.11 for Ubuntu, Windows, and macOS.
