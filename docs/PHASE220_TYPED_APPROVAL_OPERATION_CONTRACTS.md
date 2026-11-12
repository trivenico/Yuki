# Phase 220 — Typed approval operation contracts

## Implemented in this increment

- Added `core.approval_operation.ApprovalOperation`, a frozen typed envelope with an explicit schema version, stable operation type, capability, target and canonical payload SHA-256.
- Added deterministic canonical JSON encoding with finite-number enforcement, UTF-8 validation, object-only payloads, string-only object keys, strict JSON value types, a 32-level nesting limit and a 256 KB encoded payload ceiling.
- Follow-up hardening rejects tuple coercion, custom object values and non-string keys rather than allowing JSON serialization to silently normalize ambiguous inputs.
- Added exact verification helpers for payload bytes and policy-request capability, target and argument digest.
- Added tests for mapping-order stability, payload mutation, policy-request mismatch, invalid numeric data, size limits and unsupported schema versions.

## Security boundary and current limitation

This is the typed contract foundation, not yet end-to-end execution enforcement. Existing durable skill invocations still carry a text argument and the policy request factory computes the argument digest. Existing preview renderers currently receive only a `PolicyRequest`; they do not yet construct or persist a typed operation envelope.

Before this phase can be considered complete, capability adapters must:
1. Parse and validate raw task input into a capability-specific typed payload.
2. Construct an `ApprovalOperation` from the exact normalized payload.
3. Render the owner preview from that typed payload (not from model-generated prose).
4. Persist the operation envelope and preview snapshot with the approval request.
5. Bind both snapshots into the approval digest.
6. Reconstruct/validate the same typed payload immediately before execution and compare its digest with the approved envelope.
7. Refuse dispatch on schema mismatch, adapter-version drift, payload mutation or missing typed contract.

No generic adapter may infer destructive semantics or invent fields. Each capability must provide a reviewed schema and preview implementation.

## Verification

No local tests were run through this GitHub-only workflow. The new tests must pass on the exact PR head in CI. This increment is not production certification.
