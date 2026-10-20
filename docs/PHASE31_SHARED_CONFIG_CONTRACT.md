# Phase 31 — Shared Onboarding Configuration Contract

## Audit finding
Onboarding configuration defaults and nested section types were embedded directly in `core/onboarding.py`, while runtime YAML had a separate schema and a limited bridge. This duplicated the contract and made drift likely.

## Fix pushed
- Added `core/config_schema.py` with typed `TypedDict` contracts for onboarding, security, daemon, messaging, and MCP server configuration.
- Added a single onboarding-default normalizer that deep-copies input and merges safe section defaults.
- Updated `core.onboarding.validate_config` to call the shared normalizer after validation.
- Added a regression test comparing the validator output to the shared normalization contract.

## Scope / known gap
This is the first extraction step, not full schema unification. `core/config.py` still has a distinct legacy runtime YAML schema. The JSON-to-runtime bridge intentionally rejects cloud providers and capabilities it cannot honor. YAML migration, provider parity, and end-to-end application path contract tests remain open.

## Verification
Phase 30 workflow run #1204 passed on commit `e4c625a148b8786218817110d0fba98f157abaef`. Phase 31 changes are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Route runtime JSON/YAML through a canonical validated representation without silently dropping settings.
2. Implement reversible legacy config migration with backup, atomic replacement, rollback, and idempotency.
3. Ensure advertised provider/capability settings map to real runtime behavior or fail closed.
4. Add contract tests across CLI/TUI, providers, MCP, daemon, and scheduler.
5. Run current-head CI, lint/type checks, dependency and secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This extracts and tests a shared onboarding contract but does not demonstrate end-to-end production readiness.