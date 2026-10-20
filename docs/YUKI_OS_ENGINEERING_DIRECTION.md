# Yuki OS — Initial Engineering Direction

**Status:** Initial direction for engineering; revisit decisions through explicit architecture review.

## Product target

Yuki is a desktop-first agentic operating environment for Windows, macOS, and Linux. The first flagship workflow set targets developers and cybersecurity professionals: repository-aware engineering assistance, local developer workflows, defensive security analysis, and tightly controlled execution of approved actions.

## Runtime and model strategy

- **Hybrid inference:** local models are the preferred route for private, offline, and workstation-bound tasks. Configured cloud models may be used only for allowed task classes and only after a clear user-facing approval step describing what will leave the device.
- **Explicit data boundaries:** route prompts and context by sensitivity; do not silently fall back from local to cloud when a local provider fails.
- **Auditable routing:** log provider choice, task class, and approval outcome without writing secrets or full sensitive prompts to logs.
- **Graceful offline mode:** core UI, local tools, and local-model workflows should remain usable without cloud access.

## Execution and trust

- Default to read-only analysis. Separate planning, proposing, approving, and executing.
- Every state-changing action must pass capability checks and payload-bound approval at the final execution boundary.
- Apply least privilege, explicit capability grants, bounded inputs/outputs, timeouts, and cancellation.
- Do not equate subprocess execution with a sandbox. Untrusted/generated skill code remains disabled until platform-specific OS isolation is enforced and tested.
- Do not use an unverified `sandbox_enforced` declaration as proof of isolation; it is only a trusted composition assertion pending implementation of a verified backend.

## Desktop architecture

- Shared Python core and versioned skill contracts.
- Thin desktop UI shell, with OS-specific adapters behind explicit interfaces.
- Platform-specific sandbox implementations rather than one abstraction that hides different security guarantees.
- Durable local state with private filesystem permissions, schema migrations, recovery behavior, and auditable approvals.
- Optional cloud provider adapters behind a policy-enforced model router.

## Near-term milestones

1. Keep production execution fail-closed while legacy direct invocation paths remain.
2. Build and test real least-privilege sandbox backends for Windows, macOS, and Linux.
3. Adapt built-in skills to versioned manifest input/output contracts and route all runtime invocations through one approved facade.
4. Add integration and adversarial tests for approval replay, payload mutation, restart/recovery, sandbox escapes, resource exhaustion, and cloud data disclosure.
5. Gate release on latest-head CI, dependency/security scans, cross-platform tests, and independent security review.

## Non-goals for the first release

- Replacing the operating system kernel.
- Unbounded autonomous shell access.
- Silent cloud escalation.
- Treating a passing unit-test workflow as evidence that OS isolation or production readiness has been established.
