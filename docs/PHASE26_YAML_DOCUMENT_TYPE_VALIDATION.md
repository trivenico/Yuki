# Phase 26 — Reject Non-Mapping YAML Configuration

## Audit finding
The YAML loader used `yaml.load(...) or {}`. This coerced explicit YAML values such as `null`, `false`, and empty lists into an empty configuration mapping, silently selecting defaults instead of rejecting malformed configuration.

## Fix pushed
- Only a truly empty YAML file is treated as an empty mapping that uses defaults.
- Explicit YAML null, false, lists, and other non-mapping documents now reach the existing mapping-type check and fail closed.
- Added regression tests for null, false, list, and sequence documents, plus a test that an actually empty file still uses defaults.

## Verification
Phase 25 workflow run #1174 passed on commit `7b82f58f26f1e655a9ed68526094e1736abea368`. Phase 26 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Extract one canonical, versioned schema/normalizer shared by onboarding and runtime.
2. Implement reversible legacy YAML/JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure every accepted provider/capability has real runtime support or fails validation at save time.
4. Test config parity through CLI/TUI, provider, MCP, daemon, and scheduler entry points.
5. Run full current-head CI, lint/type checks, dependency and secret scans, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This closes a fail-open defaulting issue for malformed YAML; it does not establish overall production readiness.