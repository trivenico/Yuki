# Phase 160 — Provider status and model visibility

## What changed

- Added authenticated `GET /v1/providers` endpoint.
- Endpoint uses the existing local-only provider catalog; it does not probe remote providers or enumerate cloud models.
- API response is explicitly projected onto safe metadata fields. API key values, environment variable names, private endpoints, and arbitrary provider-manager fields are excluded.
- Web chat header shows the selected provider, configured model, readiness hint, and privacy classification.
- Provider metadata is refreshed on login and when restoring a browser session.
- Added API sanitization/authentication tests and runtime dependency-injection tests.

## Readiness semantics

A provider's `ready` field means its local configuration prerequisites appear to be present. It is not a live connectivity test and does not guarantee that credentials are valid, a remote service is reachable, quota is available, or a model supports every requested capability. For example, Ollama readiness is based on configured model metadata and does not probe the local server.

The UI is intentionally read-only in this phase. Active provider selection still comes from server configuration at process startup; a browser cannot switch providers or inject model names into requests. This prevents an authenticated client from changing server-wide provider policy or silently routing data to a different privacy boundary.

## Next steps

1. Implement explicit server-side provider profiles with validated model IDs and per-profile privacy policy.
2. Add a user-authorized selection operation with audit events and clear local/cloud consent.
3. Add bounded health checks that never return provider credentials or raw error payloads.
4. Add exact usage accounting where provider usage metadata is available; label estimates distinctly.
5. Build model-specific capability and context-window metadata from verified adapter support rather than marketing assumptions.

## Security notes

The endpoint requires a valid bearer session. Provider catalog output is treated as public metadata only after an explicit field allowlist. The catalog makes no network requests and does not expose secrets. Dynamic model/provider switching is not implemented by this change.
