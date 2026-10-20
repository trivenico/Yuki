# Phase 20 — Runtime Configuration Null and Allowlist Integrity

## Audit finding
Review of the shared configuration-loader path found that explicit YAML `null` values could be treated as defaults by the bounded-string helper. Runtime allowlist normalization also silently deduplicated duplicate entries, obscuring configuration mistakes and making validation behavior inconsistent with onboarding.

## Implemented
- Explicit YAML nulls now fail string validation; defaults remain applied only where a key is absent and the caller supplies a default.
- Runtime allowlists now reject duplicate values after whitespace normalization instead of silently deduplicating them.
- Added regression tests for null `name`, `ollama_model`, and `workspace` values and duplicate `web_allowed_hosts` entries.

## Verification
Changes and tests are committed to the feature branch. A successful workflow on the prior commit is not sufficient; check the GitHub Actions result for this exact head before declaring this phase green.

## Remaining release gates
1. Define a canonical versioned configuration schema and reversible YAML-to-JSON migration with backup/rollback.
2. Route onboarding and runtime loading through the same schema implementation or prove parity with contract tests.
3. Expand validation tests to all keys, env overrides, and nested settings.
4. Run exact-head CI, lint/type checks, dependency and secret scanning, and integration tests.
5. Keep PR #35 in draft until release gates and human security review pass.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This phase closes two input-validation inconsistencies but does not establish complete runtime or deployment readiness.