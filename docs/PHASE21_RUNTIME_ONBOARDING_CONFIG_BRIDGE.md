# Phase 21 — Runtime and Onboarding Configuration Bridge

## Audit finding
The runtime loader read `config.yaml` by default while onboarding wrote `~/.yuki/config.json`. As a result, a successful first-run wizard could save settings that the normal application startup did not read. Simply preferring JSON without a translation layer would be unsafe because the schemas differ and some features are not integrated.

## Implemented
- Runtime path resolution now prefers `~/.yuki/config.json` when it exists, unless `YUKI_CONFIG` explicitly selects another file; repository `config.yaml` remains the backwards-compatible fallback.
- Added a validated onboarding-JSON-to-runtime-config bridge.
- The bridge currently maps only `mock` and `ollama` provider settings that have an implemented runtime equivalent.
- The bridge fails closed for cloud providers, enabled MCP servers, enabled daemon/messaging settings, and security capabilities not supported by the integrated runtime path. It does not silently enable or discard those features.
- Added tests for JSON bridge mapping, unsupported-capability rejection, JSON path precedence, and legacy YAML fallback.

## Limitations
This is an explicit compatibility bridge, not yet a complete canonical configuration service. The current runtime schema remains narrower than the onboarding schema. Settings such as model, base URL, and provider are translated; other onboarding features must remain disabled until their runtime integration is implemented and tested.

## Verification
Changes are pushed. CI must be checked against the exact latest commit before marking this phase verified.

## Next release gates
1. Extract a shared versioned schema/normalization layer consumed by both onboarding and runtime, rather than maintaining parallel validators.
2. Add a reversible YAML-to-JSON migration with backup, atomic replacement, rollback, and idempotency tests.
3. Expand the bridge only alongside real runtime support for providers, MCP, daemon, and messaging.
4. Run full exact-head CI, lint/type checks, dependency and secret scans, and security review.
5. Keep PR #35 in draft until release gates pass.

## Readiness estimate
Keep the estimate at 40% (engineering estimate, medium-low confidence). The path mismatch is fixed for supported settings, but feature parity and end-to-end production readiness remain unproven.