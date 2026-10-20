# Phase 29 — Portable Environment Variable References

## Audit finding
The onboarding validator used Python `str.isidentifier()` for API-key environment variable names. That accepts Unicode identifiers and does not precisely match portable ASCII environment-variable naming conventions. It could approve names that deployment shells or operating environments handle inconsistently.

## Fix pushed
- Validate API key environment references against `^[A-Za-z_][A-Za-z0-9_]*$`.
- Apply the same validation in both config validation and interactive onboarding.
- Added regression coverage for leading digits, punctuation, whitespace, and Unicode lookalikes, while retaining a valid `OPENAI_API_KEY` case.

## Verification
Phase 28 workflow run #1192 passed on commit `6b04a2dfdd640747f85e0d9156bdd54f39c17c24`. Phase 29 commits are newer and require a fresh current-head CI run.

## Remaining production gates
1. Extract a canonical versioned schema/normalizer consumed by onboarding and runtime.
2. Implement reversible YAML/JSON migration with backup, atomic replacement, rollback, and idempotency.
3. Ensure each accepted provider/capability is implemented or rejected at save time.
4. Route CLI/TUI/provider/MCP/daemon/scheduler paths through validated configuration and shared authorization.
5. Complete messaging, desktop wrapper, supervised daemon, health/readiness, graceful shutdown, monitoring, and restore drills.
6. Test concurrency, queue crash recovery, idempotency, approval replay/tampering, migration failure, and authorization bypasses.
7. Run type/lint checks, dependency/secret scans, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This patch improves environment portability but does not establish end-to-end production readiness.