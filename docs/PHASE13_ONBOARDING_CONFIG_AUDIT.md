# Phase 13 — Onboarding Configuration Validation

## Implemented in this phase
- Validate first-run configuration both when saving and when loading an existing file; malformed existing files are not silently accepted or overwritten.
- Reject unknown top-level keys, unsupported providers/versions, invalid provider endpoints, non-HTTPS MCP endpoints, embedded URL credentials, query strings/fragments, malformed API-key environment references, invalid security toggles, invalid daemon polling intervals, and oversized collections.
- Recursively reject persisted secret-value fields instead of checking only the top-level object.
- Require a named environment-variable reference for cloud providers; never prompt for or persist the API-key value itself.
- Keep network, file writes, shell, MCP activation, daemon execution, and messaging disabled by default.
- Preserve atomic writes, restrictive POSIX permissions, file fsync, and directory fsync where supported.
- Add regression tests for existing invalid configs, nested secret values, unsafe URLs, and missing cloud credential references.

## Explicit limitations
- The current runtime configuration is still YAML through `core/config.py`; the JSON onboarding document is not yet a single source of truth for the runtime. This phase validates the JSON boundary but intentionally does not silently migrate YAML or change runtime config selection.
- Provider validation callbacks are optional; without one, configuration validation is structural and does not prove that a provider is reachable or that credentials are valid.
- URL syntax checks are not a substitute for network-time DNS/IP validation. MCP runtime calls must continue to use the hardened MCP client and an explicit hostname/tool allowlist.
- No full test suite was executed in the GitHub environment by this phase. A commit success is not test evidence.

## Next release gates
1. Implement a versioned YAML-to-JSON migration with backup, atomic switch, rollback, and compatibility tests, then route every runtime entry point through one config loader.
2. Add schema validation for all nested configuration keys and per-provider URL/model requirements.
3. Add provider health checks that never log secrets and have bounded timeouts.
4. Run full CI against the exact head, add lint/type/dependency/secret scanning, and fix any failures.
5. Exercise configuration, tool authorization, MCP, daemon, scheduler, and CLI paths in end-to-end tests.
6. Keep the PR in draft until current-head CI and security review pass.

## Readiness assessment
Estimated production readiness remains **40% (medium-low confidence)**. The onboarding boundary is safer, but this change does not resolve the config split or complete provider, MCP, daemon, messaging, desktop, and deployment integration. Do not represent Yuki as 100% production-ready until all release gates are evidenced as passing.
