# Phase 25 — Strict Configuration Version Types

## Audit finding
Python considers `True == 1`. The onboarding validator compared `version` with integer `1` using equality alone, which accepted JSON `true` as configuration version 1. Float `1.0` was also accepted despite the version field being an integer schema field.

## Fix pushed
- Require `version` to be an integer, explicitly reject booleans, and accept only the supported value `1`.
- Added regression tests for boolean and floating-point versions.
- Existing strict duplicate-key JSON parsing remains in place.

## Verification status
Phase 24's test workflow passed on commit `3a687d669d72a7f95a0343594dcb9327d86d0773`. Phase 25 changes are newer and require a fresh current-head CI result.

## Remaining production gates
1. Extract one canonical, versioned configuration schema/normalizer shared by onboarding and runtime.
2. Implement reversible legacy YAML-to-JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure every accepted provider/capability has real runtime support or fails validation at save time.
4. Test configuration parity through CLI/TUI, provider, MCP, daemon, and scheduler entry points.
5. Run full current-head CI, lint/type checks, dependency and secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This patch closes a type-validation bug but does not establish overall production readiness.