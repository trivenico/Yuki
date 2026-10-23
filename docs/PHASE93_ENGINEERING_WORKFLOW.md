# Phase 93 — end-to-end engineering workflow

## What this composes

EngineeringWorkflow coordinates four explicit stages:
1. Read-only workspace inspection using the Phase 91 adapter and authorized tool registry.
2. Applying a supplied, explicit FileChange proposal using Phase 92's digest-bound approval and allow-listed paths.
3. Running a configured Python unittest module using the Phase 92 test runner and its separate approval.
4. Recording workflow stages through a fail-closed audit callback.

The orchestrator does not generate edits itself, auto-approve changes, invoke model-generated tools, or run arbitrary shell commands. The caller must provide the proposal and test target; the existing service callbacks must be wired to trusted authentication, authorization, approval and audit implementations.

## Failure behavior

Inspection failure stops before edits. Edit failure stops before test execution. Test failure is reported as tests_failed with bounded result output; a successful test run is the only route to succeeded. Workflow audit failures raise and stop the sequence. Since this workflow coordinates filesystem edits and test execution rather than a single transactional system, an operator may need to inspect the workspace if an operational failure occurs after an edit.

## Security boundary

The test runner is not a sandbox. Python test modules run with the Yuki process permissions and may access the filesystem, network, or subprocess APIs. Use only trusted test modules in a controlled development environment; run untrusted project code in a separately hardened container or VM with no secrets and restricted network access.

This is an opt-in service, not a hosted API endpoint or a general autonomous coding agent. Production deployment still requires real authentication/session integration, resource-level authorization, approval UI, isolated execution, backup/recovery, metrics/alerts, and deployment-specific operational validation.

## Validation

The dedicated test suite covers a successful inspection-to-edit-to-test path and confirms that unauthorized inspection and disallowed tool requests stop before edits. CI runs Python 3.11 tests on Ubuntu, macOS and Windows. Merge only after exact-head required checks pass.
