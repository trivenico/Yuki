# Workload Identity Trust Architecture

**Status:** Design and implementation gates  
**Scope:** Specialist agents, task workers, guarded tools, and delegated workloads  
**Dependency stack:** Control-plane PRs #1177–#1180  
**Security posture:** No authenticated workload credential is implemented by this document.

## Executive finding

Yuki now has a typed authorization request, principal-aware guarded-tool enforcement, and task lease checks. The task lease still identifies its owner with a caller-supplied worker ID string. That string is useful for lease ownership and concurrency control, but it is not proof of which process is making a request.

The repository has reusable cryptographic and key-custody building blocks for OAuth credential encryption:

- `AESGCMCredentialCipher` uses AES-GCM and a host-supplied key provider.
- `OSKeyringOAuthKeyProvider` stores symmetric encryption keys in an explicitly selected OS credential store.
- The `cryptography` package is currently an optional production-storage/desktop dependency, not a universal runtime dependency.

These components do **not** implement workload signing, issuer trust, public-key distribution, key rotation for asymmetric identities, credential revocation, or replay prevention. OAuth userinfo identity is also not a workload credential issuer. Reusing OAuth AES keys as signing keys would conflate unrelated trust domains and is explicitly out of scope.

## Threat model

Protect against:

1. A caller spoofing another worker's `worker_id` to update, complete, fail, or write artifacts for a task.
2. A compromised or misconfigured agent presenting another agent's identity or permissions.
3. A credential being replayed for another task, tenant, environment, or service.
4. A stale credential remaining usable after expiry, key rotation, agent disablement, or revocation.
5. A delegated agent exercising permissions broader than the delegator granted.
6. An untrusted task payload or model output influencing identity, issuer, key selection, or authorization claims.

Out of scope for the initial implementation: protecting a fully compromised host/kernel or preventing a workload with unrestricted access to another workload's private key from impersonating it. Those require host isolation and key custody controls beyond an application token verifier.

## Trust boundaries

- **Issuer:** trusted control-plane service that authenticates a workload at bootstrap and issues short-lived credentials.
- **Workload:** specialist worker or service presenting a credential. It never chooses its own trusted issuer or verification key.
- **Verifier:** trusted application boundary that validates signature and claims before constructing a `Principal(kind="workload")`.
- **Policy engine:** evaluates the typed authorization request. A valid credential is identity evidence, not permission to perform an action.
- **Task store:** enforces lease ownership and expiry atomically. It receives a principal identifier derived from verified claims, not an untrusted worker label.
- **Delegation authority:** constrains child workload authority to the delegator's approved subset and explicit task/resource scope.

## Credential profile (proposed)

Use a compact signed token profile with an explicit, allow-listed asymmetric algorithm supported by the maintained `cryptography` library. Ed25519 is the preferred initial profile if supported by the deployment's cryptographic policy. Do not implement signature primitives, key parsing, or constant-time comparisons manually.

Required protected header fields:

- `typ`: a Yuki-specific workload credential media type.
- `alg`: exact allow-listed algorithm; reject `none`, unknown algorithms, and algorithm/key-type mismatches.
- `kid`: bounded key identifier resolved only from the configured issuer key set.

Required claims:

- `iss`: exact trusted issuer identifier.
- `sub`: stable workload principal identifier.
- `tenant_id` and `agent_id`: tenant and registered agent binding.
- `aud`: exact intended Yuki service/runtime audience.
- `iat`, `nbf`, `exp`: bounded validity window with explicit clock-skew policy and short maximum lifetime.
- `jti`: high-entropy unique credential identifier for replay/revocation controls.
- `task_id`: exact task binding for task-scoped credentials.
- `lease_id` or lease generation: binds credential to the current lease incarnation, not merely a reusable worker label.
- `scope`: bounded, explicit capability set; never treat manifest metadata as granted authority.
- `delegation_id` and parent identity when delegated execution is used.

The issuer must construct claims from authenticated registry and lease state. Never accept claims copied from an agent-supplied request as authoritative.

## Verification and enforcement requirements

1. Parse a strictly bounded token; reject duplicate JSON members, unsupported encodings, malformed segments, and unexpected header/claim types.
2. Resolve `iss` and `kid` against a host-configured issuer/key registry. Never fetch arbitrary key URLs from token headers.
3. Verify the signature before trusting any claim.
4. Enforce exact issuer, audience, subject, tenant, agent, task, lease generation, scope, and validity-window matches.
5. Enforce `jti` revocation and replay policy. For reusable short-lived workload credentials, replay protection must be combined with task/lease binding and idempotent request semantics; one-time credentials require atomic consumption.
6. Construct `Principal(kind="workload")` only after all checks pass. The principal must be immutable and carry only registry-backed identity and allowed permissions.
7. Pass the resulting principal into `AuthorizationRequest`; evaluate policy for every protected action. Credential scopes are an upper bound, not an allow decision.
8. Persist the verified principal ID and lease generation with task ownership. Add atomic store predicates so stale credentials cannot mutate a newer lease.
9. Fail closed on unavailable issuer metadata, unknown keys, revoked credentials, stale policy revision, malformed claims, verifier errors, or replay-store failure.
10. Never log raw credentials, private keys, refresh material, or full signed tokens. Audit only bounded identifiers, key ID, issuer, decision ID, and reason code.

## Key lifecycle and deployment prerequisites

Before enabling workload issuance, provide a dedicated asymmetric signing-key provider contract with:

- private-key generation/import performed by a trusted host bootstrap or KMS/HSM;
- private-key operations that do not return private key material to agents or model context;
- public-key publication/distribution to verifiers through pinned configuration or an authenticated control plane;
- overlapping rotation windows and explicit key retirement;
- issuer/key revocation and emergency rotation;
- per-environment and per-tenant separation where required;
- audit events for issue, renew, rotate, revoke, and failed verification.

The current OS keyring adapter is scoped to OAuth AES encryption. It must not be silently repurposed as an asymmetric signing-key service. If a deployment has no supported secure key custody backend, workload credential issuance must remain disabled.

## Integration sequence

### WI-1 — Cryptographic provider contracts
Define signer and verifier interfaces, issuer/key-set models, key lifecycle states, bounded claim schemas, and deterministic clock injection. Add tests using ephemeral test keys. Do not add a production issuer yet.

### WI-2 — Credential verification
Implement strict token parsing and signature/claim verification using the selected maintained crypto library. Test malformed tokens, algorithm confusion, unknown issuer/key, expired/not-yet-valid credentials, audience mismatch, tenant/agent/task/lease mismatch, revoked `jti`, and replay.

### WI-3 — Issuance and rotation
Implement issuer-side authentication, short-lived issuance, renewal, rotation overlap, revocation, and audit events behind an explicit host key-provider interface. No insecure local-file or environment-variable private-key fallback.

### WI-4 — Task lease binding
Replace trust in caller-supplied worker labels at protected boundaries with verified principal identity and lease generation. Keep legacy worker-ID compatibility only behind an explicit migration boundary; do not silently treat it as authenticated.

### WI-5 — End-to-end authorization
Thread the verified principal through coordinator dispatch, task claim/renew/progress/artifact/complete/fail, guarded tool calls, and delegated execution. Ensure approval and policy decisions bind to the same principal, task, resource, and policy revision.

### WI-6 — Operational readiness
Add key rotation/revocation runbooks, metrics, audit correlation, deployment configuration validation, and CI coverage. Enable enforcement only after all protected call sites are migrated.

## Acceptance gates

- No protected task mutation accepts an unauthenticated worker label as proof of identity.
- A credential for one task, tenant, agent, audience, or lease generation cannot be replayed in another.
- Revoked and expired credentials are rejected even when a signature remains valid.
- Unknown issuers, keys, algorithms, and policy revisions fail closed.
- Delegated scopes cannot exceed the parent grant or registered agent policy.
- Signing private keys never leave the trusted key provider.
- All coordinator, worker, task-runtime, guarded-tool, and delegation call paths are covered by integration tests.
- CI tests and security review pass before merge; deployment remains a separate explicit operation.

## Current implementation boundary

- WI-5M adds mandatory central authorization to the paired-device inference worker: a trusted device workload principal must authorize `inference:claim` before queue consumption and `inference:execute` immediately before model-provider invocation. The audited execution request binds the job ID, model ID, and a digest of the input; raw prompt content is not copied into the authorization resource. Audit or policy denial blocks provider execution.
- WI-5N adds `AuthorizedProviderAgentAdapter`, which authorizes every direct chat and streaming provider invocation through the shared gateway. It binds the trusted principal, provider/model identity, transcript/tool digest, task/delegation/approval context, and expected policy revision. The audit resource contains only a SHA-256 fingerprint, never prompt content. The strict conversation runtime now rejects owner-scoped model adapters that are not bound to the exact shared gateway and authenticated owner principal; streaming must use that same adapter. This is a production composition gate, not an automatic retrofit for legacy runtimes.
- WI-5O adds `PrincipalPermissionPolicy` and `SQLiteAuthorizationAuditLog`. The policy allows only exact actions present in a host-resolved Principal permission set and pins a policy revision. The audit writer commits every decision before returning it, uses an append-only SQLite table with update/delete triggers, records UTC timestamps, and hash-chains events. Audit failures remain deny-by-default. The installed persistent CLI chat and one-shot `ask`/`provider-test` paths now use authorized provider adapters; the web launcher now starts the API in strict central-authorization mode with owner-scoped principals and adapters. Global provider switching is separately authorized as `inference:provider:select` after the existing admin and explicit cloud-consent checks.

The implementation has progressed beyond the original #1177–#1180 foundation:

- #1183 defines bounded workload claims and signer/key-resolver/state contracts.
- #1184 verifies Ed25519 compact credentials with strict parsing, trusted key resolution, audience/task/lease binding, revocation, and one-use replay protection.
- #1185 adds a host-side issuer boundary backed by injected workload identity, lease, and signer authorities.
- #1186 binds credentials to task lease generations and fences mutations against lease-owner/generation changes.
- #1187 adds the shared authorization gateway, audited decisions, MCP and conversation tool-path wiring, verified workload-principal authorization, persisted task approval IDs, and ApprovalCoordinator execution checks.
- WI-5E adds a SQLite-backed delegation authority with opaque grant IDs, exact grantee/tenant/agent/task/scope binding, 15-minute maximum grants, bounded parent chains, scope/expiry attenuation, and ancestor revocation. TaskRuntime verifies a delegation ID from signed workload claims before authorizing the worker mutation. Strict deployments can set `require_workload_delegation=True` to reject otherwise-valid workload credentials that omit a delegation.
- SpecialistAgentCoordinator can issue a task-scoped delegation only after central `agent:delegate` authorization, using the authenticated grantor principal and a persisted subtask/agent assignment.
- Delegation issuance now resolves the specialist's workload Principal through a separate trusted host callback. The grant's grantee is the authenticated workload subject (not merely the registry agent ID), and tenant, agent ID, and requested permissions must match the parent grantor's tenant and the registered specialist.
- WorkloadCredentialIssuer now verifies any supplied delegation ID against the authoritative registry before signing. Strict deployments can require a delegation at issuance; unknown, revoked, expired, mismatched, or over-scoped grants are not signed into credentials.
- Delegation lifecycle events (issued, verified, revoked) are persisted in the delegation database with actor, task, scope, and timestamp correlation. Verification audit-write failure fails closed.
- Delegation grants now bind to the active task lease generation. Issuance resolves the current lease in the trusted coordinator; credential issuance and every protected task mutation verify the same generation across the complete parent chain. Existing database rows are migrated with a nullable generation and are intentionally invalid until re-issued.
- Protected task progress, artifact, completion, failure, and lease-renewal mutations now repeat delegation-chain verification inside the same `BEGIN IMMEDIATE` SQLite transaction that writes task state/events. This is enabled only when `WorkflowRunStore` and `DelegationAuthority` point to the same database file; TaskRuntime rejects split-database configuration when delegation authority is supplied. Revocation and mutation therefore serialize on the same SQLite write lock: whichever transaction commits first defines the order. The in-transaction verification event is committed or rolled back with the task mutation.

Important remaining limits:

- Delegation grant creation is a trusted host API. Its caller must authenticate the grantor; never expose direct grant creation to a model, agent, browser, or untrusted API payload.
- Specialist workload identity resolution must come from a trusted registry/bootstrap authority; do not derive a workload subject from the agent ID or a model-provided value.
- The delegation registry is durable SQLite authority, not a cryptographically signed delegation-token service or a distributed strongly consistent revocation service. The signed workload credential protects the opaque delegation ID; the registry remains the source of truth.
- Delegation verification and the task mutation currently span separate storage operations. A production deployment requiring zero revocation race must move the grant check and protected mutation behind a shared transactional authority or equivalent revocation fence.
- TaskRuntime retains migration compatibility unless workload verification, delegation authority, and (where all worker actions must be delegated) `require_workload_delegation=True` are explicitly configured.
- The application composition root does not yet enforce these TaskRuntime and SpecialistAgentCoordinator settings for every independently constructed instance.
- The current `PrincipalPermissionPolicy` is a narrow exact-permission policy, not a full tenant/role/ABAC policy engine. Host code remains responsible for deriving permissions from authenticated identity and configuration; never accept a Principal or permission set from model output or an untrusted request body. SQLite hash chaining detects modification of retained rows but is not a remote immutable archive or an external cryptographic anchor; production operations still need backups, restricted filesystem access, monitoring, and external audit export.
- The installed CLI and `scripts/run_web.py` are wired to central authorization. Other legacy/alternate entry points (including `main.py`, `yuki.py`, and separately assembled worker/scheduler runtimes) are not automatically covered by these composition changes and require explicit migration before being treated as production-safe. Independently constructed provider clients also require path-by-path enforcement. Streaming authorization is checked before the stream is opened; cancellation and lease revocation during an already-open provider stream are cooperative and need provider-specific lifecycle handling.
- Browser, CLI, scheduled automation, and all external tool adapters still require path-by-path enforcement and security review.

No claim is made that every Yuki action or all 100 planned specialist agents are already behind the completed control plane. CI and runtime tests must pass before merge, and deployment remains a separate explicit operation.
