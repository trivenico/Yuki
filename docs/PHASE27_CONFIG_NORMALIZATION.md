# Phase 27 — Canonical Configuration Normalization Foundation

## Audit result
Phase 26 CI passed. A deeper review of onboarding's validator found that it used `setdefault` for entire nested sections. If a user supplied a partial `security` or `daemon` object, omitted defaults were not filled, leaving consumers with an incomplete normalized shape.

## Fix pushed
- Added a single normalization path in `validate_config` that merges safe defaults into `security`, `daemon`, and `messaging` sections.
- Added an explicit default provider (`mock`) to the normalized representation.
- Defaults are constructed per call to avoid shared mutable nested state.
- Added regression tests for partial section defaults and mutable-state isolation.

## Scope note
This is a foundation for canonical configuration behavior, not yet a full schema unification. Runtime YAML has a legacy schema distinct from onboarding JSON; migration and full CLI/TUI/provider/MCP/daemon parity remain open.

## Verification
Phase 26 workflow run #1180 passed on commit `eb027ecc99300fc24932e0d2e4468945eb4bec48`. Phase 27 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Define the canonical versioned schema as a separate module and make onboarding/runtime consume it.
2. Add reversible legacy YAML-to-canonical migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure every accepted provider/capability has real runtime support or fails validation at save time.
4. Add parity tests across CLI/TUI, provider, MCP, daemon, and scheduler paths.
5. Run current-head CI, lint/type checks, dependency and secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This phase fixes normalization correctness but does not demonstrate end-to-end production readiness.