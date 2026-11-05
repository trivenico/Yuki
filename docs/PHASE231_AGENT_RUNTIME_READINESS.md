# Phase 231 — Agent Runtime Readiness Policy

## Delivered

- Corrected coordinator initialization ordering: registered agents are now built and validated before version-bound contracts are checked. This fixes a constructor-time reference to `agent_map` before assignment.
- Added a fail-closed readiness policy for specialist runtime requirements.
- Requires an exact provider ID and model ID match.
- Requires a provider report with `status=live_verified` and `live_verified=true`.
- Requires every declared provider capability to be explicitly true.
- Distinguishes disabled, undeclared, not configured, provider mismatch, model mismatch, unverified, capability mismatch, and ready states.
- Keeps the policy pure: it does not construct providers, send prompts, perform inference, or use mock providers.

## Integration boundary

This phase adds the readiness evaluator but does not yet persist provider/model requirements in the specialist registry or enforce the evaluator inside coordinator dispatch. That is the next integration step. The caller must supply readiness evidence from the real `ProviderRuntimeManager` and a matching live conformance report; a configuration catalog entry or reachability check is insufficient.

## Validation

Tests exercise policy classification using immutable readiness evidence values. They do not claim live provider compatibility or test provider implementations.
