# Phase 215 — provider-native idempotency contract

## Delivered

- Added a read-only capability inspector for provider idempotency declarations.
- Providers may declare `idempotency_mode = "native"` and expose an explicit `idempotency_key` parameter on `generate_chat`.
- The durable governed executor forwards its stable key only to providers meeting that contract.
- Providers without native support still benefit from Yuki's local durable duplicate-admission protection; internal keys are not leaked into provider request payloads.
- A declared native capability with a mismatched method signature fails closed.
- No inference is sent during capability inspection.

## Verification boundary

A declaration and matching method signature are not proof that a provider actually honors idempotency. Live provider conformance must verify same-key replay behavior and provider reference stability. No concrete provider is certified native by this phase.

## Recovery boundary

Expired requests remain uncertain and are never automatically replayed. Workflow-level reconciliation still needs an application service that authenticates operators, verifies provider evidence, and records correlated task/operation transitions.
