# Phase 224 — universal model-provider platform

## Objective

Expose one provider/model abstraction to Yuki's CLI, authenticated API and future desktop client. Users may connect vendor-native providers or arbitrary explicitly trusted endpoints that implement a supported protocol, choose exact models, and route work without changing Yuki's agent runtime.

## Existing foundation

The repository already contains:
- OpenAI-compatible chat completions adapter with bounded payloads, tool-call validation and redirect rejection.
- Ollama and Gemini CLI adapters.
- Provider manager, sanitized catalog, exact model capability registry and specialist routing.
- CLI provider selection and authenticated API provider catalog/selection hooks.

Those components are useful but do not constitute universal provider management. Several catalog entries remain unimplemented, and there is no complete desktop provider management surface.

## Phase 224 implementation

- Added `ProviderManager.register_openai_compatible` for user-supplied HTTPS endpoints and exact host approval.
- API keys are referenced only by environment-variable name. The API accepts no secret value and the catalog never returns it.
- Registered endpoints use the existing OpenAI-compatible adapter and structured agent chat contract.
- Custom endpoints appear in the sanitized model/provider catalog and capability router.
- Added CLI command:

```text
/provider add-compatible NAME | HTTPS_URL | MODEL | KEY_ENV | APPROVED_HOST
/provider select NAME
/providers
```

The command is intentionally session-only at this stage. Users configure the referenced environment variable outside the CLI. It does not write credentials or endpoint profiles to disk.

## Supported integration classes

1. **OpenAI-compatible chat completions** — arbitrary user-approved HTTPS endpoints where the endpoint genuinely implements the supported request/response and tool-call schema.
2. **Vendor-native protocols** — separate adapters are required where protocol semantics differ (for example, Anthropic Messages API, Gemini API, or provider-specific OAuth).
3. **Local inference** — explicit local runtime adapters such as Ollama; remote endpoints must not be silently classified as local.
4. **Hosted model gateways** — supported through the compatible adapter only when their actual endpoint and model semantics match the adapter contract.

Provider branding does not prove model capabilities. Tool calling, vision, audio, JSON schema, embeddings, context length, streaming and structured outputs must be verified per exact model and adapter. Unknown capabilities remain unavailable.

## Security and privacy contract

- No credential values in CLI arguments, profile metadata, logs, audit events, URLs or model catalog responses.
- Never follow HTTP redirects with authorization headers.
- Cloud endpoints require HTTPS and explicit exact-host approval. Production deployment must additionally enforce DNS/IP egress policy to mitigate DNS rebinding and private-network routing.
- Do not allow arbitrary HTTP endpoints to claim local privacy.
- Model output is untrusted input. It cannot grant tools, permissions, approval, filesystem access or device access.
- Yuki's policy engine, typed operation approval and execution guards remain authoritative across every provider.
- Cloud use is an explicit privacy choice; no implicit provider fallback.
- Never assume a provider/model supports tool calling merely because it accepts a chat request.

## Next required phases before a complete product

### 225 — persistent profiles and secure credentials
- Versioned, owner-scoped provider/model profile store.
- OS credential vault integration (Windows Credential Manager, macOS Keychain, Linux Secret Service) with injectable enterprise KMS interface.
- Secret rotation, revocation, export/import without plaintext secrets, atomic profile updates and migration.
- Never persist API keys in YAML, JSON, SQLite plaintext, shell history or desktop settings.

### 226 — model discovery and capability verification
- Provider-native model list endpoints with bounded timeouts, pagination and rate-limit handling.
- Exact model capability evidence and timestamps.
- Explicit user refresh; no automatic selection of unreviewed models.
- Safe connection test that does not invoke tools or send conversation history.

### 227 — shared authenticated provider API
- Owner-scoped list/create/update/delete/test/select endpoints.
- Secret write-only semantics; no secret readback.
- Per-user profiles, admin/global defaults only where explicitly intended.
- Audit, authorization, rate limits, CSRF/session protections and concurrent update semantics.

### 228 — desktop provider manager
- Provider cards, custom endpoint editor, model browser, credential status, connection test, capability details and privacy confirmation.
- Exact same backend contracts and model registry as CLI.
- Accessible UI, offline/error states and safe secret entry.
- No desktop app currently exists in the repository's delivered product surface; this phase is a future integration target, not a claim that a desktop UI is already available.

### 229 — unified runtime routing
- Per-conversation, per-agent and per-task model selection.
- User-controlled local-first routing, explicit cloud consent and deterministic fallback.
- Model-specific context budgeting, rate-limit-aware retries, cancellation, streaming normalization and usage/cost ledger.
- Evaluation gates for tool-call correctness, refusal behavior, prompt-injection resilience and model regressions.

## Acceptance criteria

- Same registered profile resolves to the same provider/model contract in CLI and desktop/API.
- No credential material leaks through logs, API responses, catalog metadata, errors or process arguments.
- Unknown model capabilities fail closed.
- Provider outage cannot silently route prompts to another provider or privacy domain.
- Tool execution remains behind Yuki's independent policy and approval layers.
- Cross-platform tests cover profile migration, credential vault behavior, endpoint validation, cancellation, timeouts, redirects, malformed provider responses, rate limits and concurrent profile changes.
- Exact-head CI and a release/security review are required before declaring the provider platform production-ready.

## Current status

Phase 224 is an incremental implementation. Custom OpenAI-compatible endpoints can be registered programmatically and for one CLI session. Persistent profiles, secure credential storage, vendor-native adapters, provider management API and desktop UI remain unimplemented. This phase is not a production-ready universal provider platform yet.
