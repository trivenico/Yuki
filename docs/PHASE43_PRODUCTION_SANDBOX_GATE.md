# Phase 43 — Fail-closed production sandbox gate

## Repository audit

The Phase 42 implementation uses `SubprocessExecutor`, whose own module docstring states that subprocess execution is process separation, not a security sandbox. A child process still runs with the current user's operating-system privileges. The composition root has not yet migrated all legacy in-process skills to manifest dispatch, and no cross-platform OS sandbox backend is implemented in this increment.

## Change

- `SkillDispatcher.register_subprocess()` now refuses to register an executor in `YUKI_ENV=production` unless its backend explicitly declares `sandbox_enforced = True`.
- Development mode remains compatible with trusted subprocess skills while the migration is underway.
- Added regression tests for production rejection, an explicitly sandbox-declared backend, and development compatibility.

## Security boundary and limitations

This is a fail-closed **registration gate**, not a sandbox implementation. The declaration is an operator/trusted-composition assertion; it does not independently prove that a backend enforces filesystem, network, identity, syscall, and resource restrictions. Do not mark a backend as sandbox-enforced unless its platform-specific controls are actually configured and tested. No untrusted generated/plugin code should be enabled based on this gate alone.

The following remain open:
1. Implement and test real platform-specific isolation for Windows, macOS, and Linux.
2. Migrate all runtime skill invocations through the approved manifest/facade path.
3. Add cross-platform integration, restart/recovery, and adversarial tests.
4. Run all checks on the latest PR head and obtain independent security review.

## Verification

GitHub Actions is the authoritative test runner for this branch. The Phase 42 head's CI completed successfully before this increment; Phase 43 verification must use the new head's run, not the prior commit's status.
