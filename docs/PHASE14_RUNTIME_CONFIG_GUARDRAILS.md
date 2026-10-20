# Phase 14 — Runtime YAML Guardrails

## Implemented
- Runtime YAML loading now rejects unknown top-level configuration keys instead of silently ignoring possible typos or unsupported security controls.
- Runtime provider selection is constrained to the provider factory's supported set: `mock`, `ollama`, and `hybrid`.
- Runtime configuration files larger than 128 KiB are rejected before parsing.
- Added offline regression tests for unknown keys, unsupported providers, and oversized configuration files.

## Why this phase matters
The onboarding JSON validator and runtime YAML loader are still separate configuration contracts. Until a deliberate migration is implemented, each loader must validate its own input strictly. This phase improves runtime YAML fail-closed behavior without silently switching a user's active configuration source.

## Known gaps
- This does not migrate `~/.yuki/config.json` into the runtime YAML schema or make JSON the source of truth.
- The runtime still supports only mock, Ollama, and opt-in hybrid through its current factory. The additional provider names in onboarding are not proof of end-to-end provider support.
- No successful current-head CI result has been observed in the available GitHub workflow query. Tests added in this phase are not claimed to have passed until CI executes.
- The existing configuration validator should receive deeper nested type validation and environment-override integration tests.

## Next release gates
1. Define a single versioned configuration schema and an explicit, reversible migration from legacy YAML.
2. Add a single configuration service used by CLI, daemon, scheduler, TUI, desktop, providers, and MCP.
3. Add current-head CI, lint/type checks, dependency and secret scanning, and security review.
4. Exercise config migration, environment overrides, tool authorization, MCP, provider failures, and daemon recovery end to end.
5. Keep PR draft until current-head verification passes and all release blockers are resolved.

## Readiness
Overall production-readiness estimate remains **40% (medium-low confidence)**. This phase improves runtime configuration validation but does not close the integration and deployment blockers. Do not claim 100% readiness until the full release gates are evidenced as passing.
