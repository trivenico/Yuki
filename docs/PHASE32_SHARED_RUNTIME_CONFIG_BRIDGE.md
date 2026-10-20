# Phase 32 — Shared Runtime Config Bridge

## Audit finding
Phase 31 extracted onboarding defaults, but `core/config.py` still owned its own onboarding-to-runtime mapping logic. This left the support policy duplicated and made it possible for onboarding/runtime bridge behavior to drift.

## Fix pushed
- Added `onboarding_to_runtime_config` in `core/config_schema.py` as the single fail-closed mapping point for onboarding configuration to runtime-supported settings.
- Updated `core.config._load_onboarding_json_config` to call the shared mapping function and translate mapping failures into a clear startup `RuntimeError`.
- Added tests for safe Ollama mapping and rejection of cloud providers, enabled MCP, daemon/messaging, and elevated security capabilities that are not wired into the runtime.

## Scope / known gap
This does not yet migrate the legacy YAML schema to a fully canonical runtime representation. It centralizes the onboarding JSON bridge and explicitly rejects unsupported features. No settings are silently discarded, but reversible migration, schema version evolution, and all-entrypoint parity remain open.

## Verification
Phase 31 workflow run #1212 passed on commit `ace9e3337642c09f0283644aa57c3c196ee3f93d`. Phase 32 commits are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Define a canonical runtime config type covering legacy YAML and onboarding JSON, with explicit version migration.
2. Implement reversible migration with backup, atomic replacement, rollback, and idempotency tests.
3. Ensure provider/capability settings map to real runtime behavior or fail closed at save/startup.
4. Add contract tests across CLI/TUI, providers, MCP, daemon, and scheduler.
5. Run current-head CI, lint/type checks, dependency/secret scanning, integration tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). The bridge policy is centralized and tested in source, but Phase 32 CI and end-to-end production readiness are not yet verified.