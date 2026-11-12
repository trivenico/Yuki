# Phase 214 — Claim-bound durable capability approvals

## Scope

This phase introduces the first integrated path from durable task identity through central policy evaluation, owner approval, encrypted worker credential delivery and guarded skill dispatch.

## Implemented in this branch

- Restored the deterministic, default-deny `ToolPolicyEngine` contract and durable `CapabilityApprovalGateway`.
- Added durable approval inbox requests, authenticated approver checks, policy-version revalidation, exact-request digest binding, expiry, revocation and one-time atomic consumption.
- Added encrypted credential storage with private SQLite file handling. Plaintext approval tokens are not persisted.
- Bound broker records and authenticated encryption associated data to owner, goal, task, worker identity, capability, target, policy version, expiry and the SHA-256 digest of the exact active task lease token.
- Added a claim-generation digest to trusted worker policy context and to `PolicyRequest`; approvals therefore cannot be replayed against a reassigned task claim.
- Require the broker's injected claim validator to verify the live task lease before and after credential delivery and during credential resolution.
- Require central policy gateway configuration and a worker-pinned resolver whenever the durable skill runtime uses central approval.
- Revalidate the current claim before and after credential resolution; the runtime still checks the lease immediately before skill handler dispatch.
- Added gateway contract tests and broker-to-worker integration tests, including wrong-claim resolution, stale-claim delivery rejection, one-time resolution and token redaction.

## Trust and deployment requirements

- The approval inbox/API caller must derive the approver identity from an authenticated owner session. The `authorize_approver` callback is a security boundary and must not trust request-body identity.
- `validate_claim` must be wired to the same authoritative durable task engine that owns the claim. A permissive callback invalidates the broker's security guarantees.
- The `seal`/`unseal` callbacks must be implemented with a reviewed AEAD/envelope-encryption provider, authenticated associated data, key custody and rotation. The integration-test cipher is intentionally test-only and is not production encryption.
- Broker databases and gateway databases need deployment-specific private storage, backup, retention and recovery controls. The broker file hardens POSIX permissions; Windows ACL provisioning remains a deployment responsibility.
- Lease validation and external provider commits are not one atomic transaction. A claim can change after the local guard and before a remote side effect. Provider adapters still need generation-aware commit fencing, idempotency keys, outbox/reconciliation and compensation where supported.
- The gateway/broker are primitives; authenticated HTTP approval routes, user-facing approval UX, service wiring, production KMS configuration and supervised recovery hooks are not completed by this phase.

## Explicitly not production-certified

No real KMS/AEAD deployment, live provider adapter, multi-process stress test, external-effect commit fencing, production recovery exercise, penetration test or release certification is claimed. CI must pass on the exact PR head before this work can be considered validated.
