# Phase 30 — Configuration Key Type Validation

## Audit finding
The onboarding validator checked unknown keys using set subtraction and sorted the unknown values. JSON objects always have string keys, but the public Python validation API can receive arbitrary mappings. Non-string keys could cause confusing exceptions during sorting, and nested MCP/security mappings had the same gap.

## Fix pushed
- Reject non-string top-level configuration keys before unknown-key checks.
- Reject non-string keys in MCP server objects and security/daemon/messaging sections with clear `ValueError` messages.
- Add regression tests for non-string keys at top-level, security-section, and MCP-server levels.

## Verification
Phase 29 workflow run #1198 passed on commit `0eabdc15860d5f24aace8537f3c24d48923effa8`. Phase 30 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Extract a canonical versioned schema/normalizer consumed by onboarding and runtime.
2. Implement reversible YAML/JSON migration with backup, atomic replacement, rollback, and idempotency.
3. Ensure each accepted provider/capability is implemented or rejected at save time.
4. Route CLI/TUI/provider/MCP/daemon/scheduler paths through validated configuration and shared authorization.
5. Complete messaging, desktop wrapper, supervised daemon, health/readiness, graceful shutdown, monitoring, and restore drills.
6. Test concurrency, queue crash recovery, idempotency, approval replay/tampering, migration failure, and authorization bypasses.
7. Run current-head CI, lint/type checks, dependency and secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This closes a validation API robustness gap but does not establish end-to-end production readiness.