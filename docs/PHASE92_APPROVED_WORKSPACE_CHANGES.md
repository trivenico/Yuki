# Phase 92 — explicitly approved workspace changes and test execution

## Scope

This phase adds two opt-in services:
- `ApprovedWorkspaceChanges` applies bounded text replacements to existing files that are both explicitly allow-listed and unchanged since proposal.
- `ApprovedTestRunner` executes only configured Python unittest module targets.

Neither service is automatically wired into the specialist worker. A deployment must deliberately construct them with trusted identity, authorization, approval, audit, and narrowly scoped configuration.

## Change approval contract

The workspace editor requires a trusted `ToolPrincipal` with `workspace:write`, an explicit allow-list of relative file paths, the expected SHA-256 of each current file, and a positive approval callback bound to the actor, change ID, and complete proposed content digest. Files are rechecked immediately before replacement. Symlinks, parent traversal, excluded secret/config paths, new files, oversized files, duplicate paths, and more than eight edits are rejected.

The service stages writes beside each target, flushes staged content, preserves the existing file mode, and attempts rollback if a replacement or success-audit write fails. Multi-file replacement is not a filesystem-wide atomic transaction: on rollback failure, the service raises an operator-attention error. Use version control and backups for important workspaces.

## Test execution contract

The test runner requires `tests:execute`, a trusted principal, a positive approval callback bound to the selected target and its source digest, an explicit allow-list of Python unittest module names, and a timeout no longer than 120 seconds. It invokes `sys.executable -m unittest <configured-module> -v` using an argument array with `shell=False`; callers cannot supply command strings, flags, or arbitrary paths. Output is bounded and audited.

**This is not a sandbox.** Python tests execute code with the permissions of the Yuki process and may perform filesystem, network, or subprocess operations. Only run trusted repository test modules in an appropriately isolated development/CI environment. For untrusted code, use a separate hardened container or VM with no secrets, restricted network, and resource limits; this module does not provide that isolation.

## Fail-closed controls

- Trusted principal and permission checks happen before any operation.
- Approval callback exceptions or non-true responses deny execution.
- Audit preflight must succeed before edits or tests begin.
- File edits require exact content digests and bounded input.
- Test execution is allow-list-only, digest-bound, no-shell, and time-limited.
- The module is opt-in and is not registered as a model-callable tool by default.

## Validation

Tests cover approval denial, stale content, unlisted paths, traversal and symlink rejection, audit failures, bounded edits, allow-listed test execution, source-digest changes, and timeout behavior. CI runs the new tests on Python 3.11 on Ubuntu, macOS, and Windows. Merge only after exact-head CI and repository quality gates pass.
