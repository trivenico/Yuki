# Phase 22 — Duplicate JSON Key Rejection

## Audit finding
Python's default JSON decoder accepts duplicate object keys and keeps the last value. For example, a config containing both `provider: mock` and a second `provider: openai` can be interpreted differently by a reviewer or another parser. This is a configuration ambiguity at a security boundary.

## Implemented
- Added an `object_pairs_hook` parser that rejects duplicate JSON keys at every nesting level in onboarding configuration reads.
- Applied the same duplicate-key rejection in the runtime JSON configuration bridge.
- Added regression tests for duplicate top-level provider keys in runtime loading and existing onboarding configuration.

## Verification status
The code, tests, and report are pushed to `phase6/production-hardening`. CI must pass on the latest commit before this phase is considered verified.

## Remaining release gates
1. Extract a shared versioned schema/normalizer used by both onboarding and runtime.
2. Add reversible YAML-to-JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Add integration tests for every supported provider and ensure unsupported onboarding settings cannot be saved as if operational.
4. Run current-head CI, type/lint checks, dependency and secret scanning, and human security review.
5. Keep PR #35 in draft until the release gates pass.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). Duplicate-key rejection removes one ambiguity, but the project still lacks proven end-to-end integration and production deployment evidence.