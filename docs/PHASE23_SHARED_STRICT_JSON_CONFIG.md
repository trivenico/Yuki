# Phase 23 — Shared Strict JSON Configuration Parsing

## Audit finding
Phase 22 added duplicate-key rejection independently in onboarding and runtime configuration. Duplicated security logic can drift over time and weakens the guarantee that both entry points interpret JSON identically.

## Implemented
- Added `core/config_json.py` as the shared strict JSON parser.
- Onboarding and runtime configuration now call the same `loads_strict` helper.
- Duplicate keys are rejected at every nesting level, including nested security objects.
- Added tests for nested duplicate keys in the shared parser and runtime config loading.
- Removed the accidentally created helper file from the default branch; the intended implementation is on this feature branch.

## Verification
Phase 22's workflow passed on commit `711c9c052481161eb2f99961bed687d58603af8c`. Phase 23 requires its own successful CI run on the final feature-branch head.

## Remaining release gates
1. Create one canonical versioned schema/normalizer for onboarding and runtime, not just shared JSON parsing.
2. Add reversible YAML-to-JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure every accepted onboarding provider/capability has a real runtime implementation or is rejected at save time.
4. Add integration tests for CLI/TUI/provider/MCP/daemon and centralized tool authorization.
5. Run exact-head CI, lint/type checks, dependency and secret scanning, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). Shared parsing reduces drift but does not establish feature parity or end-to-end production readiness.