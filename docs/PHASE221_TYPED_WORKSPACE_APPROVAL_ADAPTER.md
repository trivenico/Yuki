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
identifiers, NUL-containing text and oversized individual files. The common
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

This is an adapter foundation, **not yet end-to-end approval enforcement**.
The gateway does not yet persist this envelope alongside an approval request;
the inbox preview is still the Phase 219 four-field capability preview; and
the durable worker does not yet reconstruct and verify this envelope before
calling `ApprovedWorkspaceChanges.apply`. The adapter must not be treated as
an authorization mechanism or used to bypass the existing gateway, owner
identity, claim fencing, workspace allow-list, old-file digest checks or audit.

The next increment must add durable operation-envelope persistence with
integrity validation, bind its digest into the approval request/preview, and
verify the exact envelope immediately before execution. It must include
migration/restart tests and substitution/TOCTOU regression tests before the
workspace capability can be called fully integrated.
