# Phase 42 — Controlled invocation facade

## Audit finding

The runtime still exposes a legacy `YukiCoreAgent.run_skill()` path that calls skill objects in-process. Manifest-dispatched skills have a separate authorization and subprocess path. The repository does not yet have one facade that all runtime callers use.

## Changes in this phase

- Added `core/invocation_facade.py` as a narrow, explicit-review entry point for manifest-dispatched skills.
- The facade asks the dispatcher to prepare a one-time token, shows the reviewer action/risk/arguments/digest/expiry, and never passes the bearer token to the review callback.
- Denial and confirmation errors revoke the prepared token; execution failures return a safe status without exposing internal exception details.
- Added `tests/test_invocation_facade.py` for review metadata, denial, exception handling, and execution failures.

## Scope and limitations

This is a first migration seam, not a completed migration. The legacy `YukiCoreAgent.run_skill()` path and `main.py` are not yet routed through this facade because the current built-in skills use a different interface and execute in-process. Silently wrapping them would falsely imply sandboxing and could break runtime behavior.

The subprocess executor is not a security sandbox: child processes run with the service user's OS privileges. Do not execute untrusted generated/plugin code until a platform-specific sandbox with filesystem/network restrictions, resource limits, and least-privilege identity is implemented and tested.

## Acceptance criteria for the next phase

1. Adapt each built-in skill to a versioned manifest and JSON input/output contract.
2. Wire the facade into the application composition root and remove direct skill execution paths.
3. Add a deployment-selected sandbox backend (e.g. rootless container or OS sandbox) and fail closed when production requests untrusted skill execution without it.
4. Add end-to-end tests proving every invocation passes through authorization and sandboxing.
5. Run all CI and security checks on the latest branch head before merging.
