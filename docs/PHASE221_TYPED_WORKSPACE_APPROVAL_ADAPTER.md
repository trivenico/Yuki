# Phase 221 — Typed workspace approval adapter

## Scope

This phase introduces the first capability-owned adapter from the existing
`FileChange` contract to the typed `ApprovalOperation` envelope. It defines
the exact JSON payload that a workspace edit must bind to:

- relative path;
- expected pre-edit SHA-256 digest;
- exact proposed UTF-8 text content.

The adapter binds that payload to operation type `workspace.write`, capability
`workspace:write`, and a stable workspace target identifier. It rejects
duplicate paths, invalid digests, non-`FileChange` inputs, invalid workspace
identifiers, actual NUL bytes and payloads exceeding the canonical 240 KB budget. The common
canonical payload validator additionally rejects ambiguous/non-JSON values,
non-finite numbers, excessive nesting and payloads above 256 KB.

## Verification contract

The operation builder returns both the immutable envelope and normalized
payload. The verifier reconstructs the payload from the proposed `FileChange`
sequence and checks operation type, capability, target and canonical payload
digest. Tests cover digest compatibility with the current policy argument
digest, changed content, changed path, changed workspace target, duplicate
paths and invalid input.

## Explicit non-completion

This is an adapter and gateway persistence foundation, **not yet end-to-end
approval enforcement**. The durable worker does not yet reconstruct and verify
this envelope before calling `ApprovedWorkspaceChanges.apply`, and the human
inbox still lacks a structured per-file diff showing the exact proposed content.
The adapter must not be treated as an authorization mechanism or used to bypass
owner identity, claim fencing, workspace allow-lists, old-file digest checks or
audit. Production workspace execution remains disabled until that integration
and its TOCTOU tests are complete.

## Durable gateway binding increment

The approval gateway now accepts a typed operation and its exact canonical
payload together. It verifies both against the policy request and envelope,
stores the immutable envelope plus an encrypted payload envelope, and exposes
only integrity-checked operation metadata in the pending inbox. Payload
ciphertext is authenticated with associated data containing the request and
operation digests. Typed requests fail closed unless an injected encryption
adapter is configured; production requires a reviewed AEAD/KMS implementation
with key custody and rotation. Duplicate live requests cannot silently switch
between typed and untyped forms or substitute a different payload.

When a typed request is approved, the gateway verifies the persisted envelope
again and binds its envelope digest to the one-shot approval credential. The
authorization API accepts the same typed envelope and refuses to consume a
typed credential through the untyped route or with a substituted operation.
A nullable additive SQLite column preserves existing legacy approval records.
Tests cover restart reads, stored-envelope tampering, request mismatch,
idempotency substitution and token-to-envelope binding.

This still does **not** complete workspace execution integration. The durable
worker does not yet build/pass the adapter envelope at authorization and
execution time, and the human preview does not yet display a structured exact
file-change diff. Those are required before this capability is enabled for
production use.

Capability-owned operation-kind allowlists are now part of the trusted preview
registry. A capability must explicitly register the operation type it accepts;
typed request creation, approval and authorization reject an unregistered or
substituted operation kind. This prevents a caller from pairing an arbitrary
operation label with a capability merely because the payload digest matches.
