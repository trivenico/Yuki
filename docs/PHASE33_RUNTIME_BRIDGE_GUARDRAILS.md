# Phase 33 — Runtime Bridge Security Invariants

## Audit finding
The shared onboarding-to-runtime bridge rejected elevated capabilities, but it did not enforce the onboarding policy `require_approval_for_unknown_tools`. It also accepted mappings without checking the declared onboarding schema version. This could make configuration claim a weaker guardrail than the current runtime bridge supports.

## Fix pushed
- Added a typed `RuntimeConfig` contract for the bridge return value.
- Require the exact supported onboarding schema version before mapping.
- Reject configurations that disable approval for unknown tools.
- Added regression tests for both the approval invariant and unsupported schema versions.

## Scope / known gap
This hardens the onboarding JSON bridge only. Runtime YAML remains a legacy configuration schema; this phase does not complete reversible migration or prove that every runtime tool path enforces the approval policy. Those require separate integration tests and security review.

## Verification
Phase 32 workflow run #1220 passed on commit `b34ab11cb8a7e755b5301dd884fd1db7e0a61d3f`. Phase 33 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Define a canonical runtime configuration model for both YAML and onboarding JSON.
2. Implement reversible migration with backup, atomic replacement, rollback, and idempotency tests.
3. Enforce tool approval at every actual execution boundary, not only during config mapping.
4. Add integration tests across CLI/TUI, providers, MCP, daemon, and scheduler.
5. Run current-head CI, lint/type checks, dependency/secret scanning, and independent security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This closes a specific bridge-policy gap but does not establish end-to-end production readiness.