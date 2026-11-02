# Phase 161 — Persistent administrator-controlled provider switching

## Included

- Add a runtime provider manager that swaps the active provider adapter only after the replacement provider has been constructed successfully.
- Persist only the selected provider identifier and schema version in `workspace/provider-selection.json`; never persist API keys, endpoint credentials, or other secrets.
- Write the selection file using a same-directory temporary file, restrictive permissions where supported, flush/fsync, and atomic replacement.
- Restore the persisted provider on startup. If it is no longer implemented, configured, or ready, startup fails closed with a repair instruction instead of silently routing to a different provider.
- Add authenticated `GET /v1/auth/me` and admin-only `PUT /v1/providers/active`.
- Require explicit confirmation of the server-wide impact. Require a separate cloud-routing confirmation for every provider not positively identified as a loopback-only local runtime. Non-loopback Ollama endpoints and hybrid routing therefore require cloud confirmation.
- Add an admin-only selector to the browser UI. The selector lists implemented providers and disables those whose local configuration prerequisites are not ready.
- Preserve existing in-flight generations on the provider object they started with; new generations use the newly selected adapter provider.

## Provider support boundary

This endpoint can select only providers that the existing factory and catalog both mark as implemented and ready. In this repository state, that means configured OpenAI, Ollama, Gemini CLI, and Hybrid profiles. The catalog may list other vendors, but they remain disabled until their adapters are implemented. Readiness is a local prerequisite check, not a live connectivity or authentication guarantee.

## Scope and operational model

Provider selection is **server-wide**, not per-user or per-conversation. Only an administrator may change it. All users on the same Yuki process will use the newly selected provider for new requests. The web launcher currently runs a single Uvicorn process; do not deploy multiple independent worker processes with separate in-memory provider managers. A future multi-worker deployment needs a shared provider-control plane and coordinated reload/broadcast.

The selector is persisted under the configured Yuki workspace. It is separate from the tracked Git repository, so secrets and local runtime state do not enter Git commits. The provider ID file is portable with the workspace backup, but the provider's credentials must be configured separately on the destination machine.

## Privacy

The UI asks for explicit global-impact confirmation before every switch. It additionally asks for cloud/remote data-routing confirmation unless the catalog positively classifies the target as `local_runtime`. The API enforces both confirmations; browser prompts are not the security boundary. Cloud credentials must be provisioned in the environment using the provider's documented secret variable.

## Validation

Tests cover admin authorization, non-admin rejection, missing confirmation, cloud consent, non-loopback Ollama consent, persisted selection restoration, invalid provider rejection, corrupt persisted state, and runtime dependency injection.

Before merging, CI must pass on the exact PR head. Before public production use, add centralized multi-process provider coordination, durable actor-attributed audit events, provider connectivity probes with strict timeouts, and a documented credential rotation process.
