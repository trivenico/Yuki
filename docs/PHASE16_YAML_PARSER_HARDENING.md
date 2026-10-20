# Phase 16 — YAML Parser Ambiguity Hardening

## Audit finding
The runtime loader rejected unknown keys and incorrect scalar types, but standard safe YAML parsing still accepts duplicate mapping keys by retaining the last value. This creates configuration ambiguity: a reviewer may read one provider/security value while the application uses a later duplicate. The previous size check also used file metadata before reading, leaving a small time-of-check/time-of-use gap.

## Implemented
- Added a yaml.SafeLoader subclass that rejects duplicate mapping keys.
- Read the configuration bytes once, enforce the 128 KiB limit on those exact bytes, decode UTF-8 strictly, then parse that same snapshot.
- Added regression tests for duplicate keys and a size-limit boundary case.
- Preserved existing allowlist/type/provider validation and environment overrides.

## Verification status
The code and tests have been committed to the feature branch. The available GitHub workflow-run query has not yet demonstrated that CI passed for the current head. Do not treat the presence of regression tests as evidence they passed.

## Remaining risks and next steps
1. Add duplicate-key and malformed UTF-8 tests to a clean local run; run the entire unit suite and capture results.
2. Resolve any branch conflict with main, re-run CI on the final head, and keep PR draft until CI is green.
3. Introduce one versioned config schema and reversible YAML-to-JSON migration; don't switch active config without backup/rollback and compatibility tests.
4. Add strict nested validation for all runtime configuration fields and tests for environment override precedence.
5. Add lint/type/dependency/secret scanning and human security review.

## Readiness estimate
Remain at 40% (medium-low confidence). This phase closes parser ambiguity but does not establish end-to-end runtime or deployment readiness.