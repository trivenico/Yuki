# WI-6F — Durable audit-anchor credential state

## Durable replay and revocation

`core.audit_anchor_credential_state.SQLiteAuditAnchorCredentialState` implements the `WorkloadCredentialState` contract consumed by `AuditAnchorCredentialAuthenticator`.

- Replay consumption uses SQLite `BEGIN IMMEDIATE` and a unique key on (issuer, token ID), so concurrent processes sharing the same local database cannot successfully consume the same credential twice.
- Revocation is persistent and checked before consumption. `revoke(issuer, token_id)` is idempotent and blocks even a credential that has not yet been presented.
- Expired consumption records are pruned during a later consume operation. Revocation records are intentionally not automatically deleted.
- The database path uses the existing private SQLite path hardening helper; POSIX files are owner-only.
- Backend errors propagate to the authenticator, which rejects the credential rather than accepting it.

## Deployment topology

Use a dedicated credential-state database path in the independent anchor trust domain. Every anchor authenticator process on the same host must point to the same local database file. SQLite locking provides local multi-process coordination; it does not provide safe cross-region coordination or a general shared network-filesystem database. Do not mount this SQLite file into unrelated hosts as a substitute for a distributed transactional store.

For multi-host or multi-region anchor deployments, implement the same `WorkloadCredentialState` contract with a managed transactional database or dedicated replay/revocation service that provides atomic consume-once semantics. All instances must share one authoritative state domain. The service must fail closed when that dependency is unavailable.

Keep the state database separate from the anchor checkpoint database and from Yuki's application audit database. Back it up with the same security classification and access controls as publisher credential metadata. Protect backups and revocation administration as security-critical data.

## Deployment and recovery drills

Before enabling production publisher traffic, verify:
1. The issuer signs a short-lived token with the configured active key and exact anchor audience.
2. The authenticator resolves only currently trusted keys and accepts the token once.
3. A second use on another worker/process is rejected as replay.
4. A revoked but previously unused token is rejected.
5. Revocation and consumed state survive process restart.
6. Loss/unavailability of the credential-state database rejects authentication.
7. Key rotation (new key distributed, signer switched, old key retired) does not accidentally re-enable retired credentials.
8. Restore of checkpoint and credential state is coordinated with the independent external checkpoint and incident recovery procedure.

Restoring an old credential-state backup can reintroduce replay acceptance for credentials that were consumed after the backup. Therefore, do not restore credential state independently without reconciling the maximum credential lifetime, revocation events and issuer key state. Prefer preserving current revocation data and replay tombstones, or rotate/retire affected signing keys and wait out the maximum five-minute token lifetime before reopening publisher access.

## Limits

This is a single-host SQLite implementation. It is not a multi-region database, immutable revocation ledger, KMS/HSM, credential issuer, network isolation policy, or deployed anchor. The host still supplies signer custody, public-key trust distribution, administrator authentication, workload bootstrap identity, TLS/mTLS, and infrastructure monitoring.
