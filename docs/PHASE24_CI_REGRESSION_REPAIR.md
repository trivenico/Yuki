# Phase 24 — CI Regression Repair

## Audit finding
The Phase 23 CI run failed: 222 tests ran and two onboarding tests errored because `run_onboarding` still called the removed private `_load_json_config` helper after the shared parser refactor.

## Fix pushed
- Updated onboarding's existing-config path to call the shared `loads_strict` function directly.
- This preserves duplicate-key rejection and fixes the stale symbol reference.
- No behavior was relaxed and no unsupported provider or capability was enabled.

## Verification
Phase 23 run #1164 failed with two `NameError: _load_json_config is not defined` errors in onboarding tests. The repair is now pushed in commit `203496726f3c04ec53a70cf39186a4cb93724000`; the new head needs a fresh CI result before Phase 24 can be marked verified.

## Next release gates
1. Confirm current-head CI passes all tests.
2. Extract one canonical versioned config schema and normalizer for onboarding and runtime.
3. Add reversible YAML-to-JSON migration with backup, atomic replacement, rollback, and idempotency tests.
4. Ensure each accepted provider/capability has real runtime support or is rejected at save time.
5. Complete integration, type/lint, dependency/secret scanning, and human security review.

## Readiness estimate
Keep at 40% (engineering estimate, medium-low confidence). This phase fixes a CI regression; it does not establish end-to-end production readiness.