# Phase 28 — Configuration State Isolation

## Audit finding
Phase 27 normalized nested section defaults, but started from `dict(config)`, a shallow copy. Nested values such as `mcp_servers` remained shared with the caller. Mutating the normalized configuration could unexpectedly mutate the caller's original input, and vice versa.

## Fix pushed
- Use `copy.deepcopy` before normalization so mutable nested caller-owned values are isolated.
- Add a regression test that mutates nested MCP tool lists on both objects and proves the objects remain independent.
- Preserve Phase 27 safe defaults and existing strict validation.

## Scope note
This is a correctness hardening step, not the full canonical schema/migration project. Runtime YAML and onboarding JSON still have different schemas, and full application-path parity remains open.

## Verification
Phase 27 workflow run #1186 passed on commit `8555a48ea7b7a846327879c025b6bf0cba07119a`. Phase 28 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Extract a canonical versioned schema and normalizer shared by onboarding and runtime.
2. Implement reversible legacy YAML/JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure every accepted provider/capability has real runtime support or fails validation at save time.
4. Add config parity tests across CLI/TUI, provider, MCP, daemon, and scheduler paths.
5. Run current-head CI, lint/type checks, dependency and secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This patch closes mutable-state aliasing but does not establish end-to-end production readiness.