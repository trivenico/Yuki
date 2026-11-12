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

## Authenticated owner review payload

The authenticated approval service now enriches the owner-only inbox with the
exact decrypted typed operation payload after checking `workflow:approve`.
For workspace writes this includes each relative path, expected pre-edit
SHA-256 and exact proposed content. The HTTP API retains its no-store response
headers and never returns approval credentials, request digests or claim
secrets. Non-approvers cannot call this inbox method. This makes the exact
proposal available to an owner-facing client, but does not itself implement
the visual diff UI or connect the worker to the workspace executor.

## Claim-fenced execution bridge

`core/approved_workspace_operation_executor.py` adds a capability-specific
bridge that reconstructs the typed envelope from `FileChange` values, checks
the request binding, consumes the matching typed central approval exactly
once, and calls the existing workspace executor only while the durable claim
remains live. `ApprovedWorkspaceChanges.apply` now accepts an optional
execution guard and checks it after staging and immediately before the
pre-commit file-digest recheck and replacement. Guard exceptions are treated
as claim loss. The bridge retains the executor's trusted-principal check,
explicit path allowlist, expected-file digest checks, audit and rollback
behavior.

Regression coverage exercises successful exact-payload execution and a claim
that is lost at the final commit guard. This is an execution primitive, not yet
a complete worker integration: `DurableSkillWorker` still accepts only its
existing `skill.invoke` payload shape and does not dispatch a dedicated
`workspace.apply` task through this bridge. Production rollout must also
supply a reviewed AEAD/KMS envelope adapter and bind the claim validator to
the worker's actual live task/worker/lease tuple.

## Durable workspace task worker

`core/durable_workspace_worker.py` adds a dedicated `workspace.apply`
worker for the fixed payload shape `{workspace_id, changes}`. It does not
dispatch arbitrary skill names, commands, scripts, or model-provided tools.
It derives owner/goal/task/claim identity from the durable engine, builds the
typed workspace operation, requires a policy request factory to bind all of
those values plus the exact payload digest, persists a claim-bound approval
wait, waits through the worker-pinned credential resolver while heartbeating,
and dispatches only through the claim-fenced executor bridge. Workspace tasks
must be single-attempt.

The task engine's safe rejection vocabulary now includes the workspace
worker's fixed invalid-payload, unsupported-task, and invalid-identity cases.

The workspace executor now revalidates the live claim, re-resolves the
workspace path (to detect symlink/path substitution), and rechecks the expected
file digest immediately before each individual replacement. If a claim is
lost partway through a multi-file operation, the existing rollback path
restores already-replaced files. Regression tests cover this mid-commit loss.

This is a dedicated worker implementation and test slice, not yet an enabled
production service. Deployment still needs a reviewed AEAD/KMS adapter, a
trusted runtime wiring the worker to authenticated task submission and the
worker-pinned broker, an owner-facing structured diff/approval experience,
and crash-safe filesystem transaction/recovery semantics for process or power
loss between multiple replacements. Until those gates are complete, do not
expose `workspace.apply` as a generally available user capability.
