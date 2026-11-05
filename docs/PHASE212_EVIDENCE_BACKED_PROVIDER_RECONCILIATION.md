# Phase 212 — evidence-backed provider execution reconciliation

## Delivered

- Added an explicit resolution operation for executions in the `uncertain` state only.
- Resolution requires an allowed terminal outcome, a bounded evidence-source identifier, a SHA-256 evidence digest, a provider reference, and an attributable resolver identity.
- State transition and immutable resolution evidence metadata are committed in one SQLite transaction.
- Duplicate resolution is prevented by a unique execution ID constraint and a conditional status transition.
- Resolution metadata is queryable by execution ID; raw evidence, prompts, credentials and provider response bodies are not stored.

## Trust boundary

The evidence digest is an integrity reference supplied by the trusted caller. It does not independently authenticate the provider, prove that the evidence is truthful, or replace authorization. The application service must authenticate the resolver and verify evidence provenance before calling this store method.

## Safety

Only uncertain executions can be resolved. No automatic retry, replay, or fallback is introduced. Existing provider idempotency keys and provider-side status verification remain responsibilities of the concrete provider adapter/application integration.
