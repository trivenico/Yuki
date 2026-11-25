# Migration control-plane storage decision

Status: architecture decision; no runtime implementation or infrastructure is provisioned.

## Context

The migration credential lifecycle requires durable, independently readable attempt state, compare-and-swap transitions, target-scoped fencing, expiry scans, and a separately supervised reconciler. The control-plane state must remain available when the application database is being migrated or is unavailable.

The repository already contains two relevant persistence components:

- `automation.workflow_store.WorkflowRunStore`: a local SQLite workflow journal with idempotency, optimistic version checks, worker leases, and task events.
- `core.oauth_credential_vault.SQLiteOAuthCredentialVault`: owner-scoped encrypted OAuth token storage that requires a host-provided AEAD cipher and a separate HMAC key.

These are valuable foundations, but their current contracts do not satisfy the migration controller's complete security and operational requirements.

## Storage formats are not interchangeable

Yuki should use each storage technology for the job it is designed to do:

| Technology | Good fit in Yuki | Not a safe default for |
|---|---|---|
| YAML | Human-edited configuration, policy defaults, agent/skill manifests, deployment templates and checked-in examples | Concurrent mutable task state, credential lifecycle records, leases, audit ledgers |
| JSON | Bounded interchange payloads, API messages and canonical request/evidence serialization | Multi-writer transactional state |
| SQLite | Local durable state on one Linux VPS: workflow journals, a single-node control-plane ledger, local indexes and transactional CAS | Multi-host shared writes, network-filesystem databases, independently surviving a loss/compromise of the VPS |
| PostgreSQL | Shared multi-process/multi-host state, strong relational constraints, concurrent transactions, indexed recovery queries and operationally managed backups/replication | Simple configuration files or every local cache/journal |

Yuki already uses YAML as a required configuration dependency and SQLite in its workflow journal. PostgreSQL is an optional production-storage dependency in `pyproject.toml`; it is not a universal requirement for every Yuki component.

**Why YAML alone is not a database:** an application would have to implement locking, atomic replacement, concurrent-writer conflict handling, compare-and-swap, unique constraints, crash recovery, durable event ordering, indexed recovery scans and backup consistency itself. A read-modify-write YAML file can lose another process's update or be left truncated by a crash unless a carefully designed transactional layer is built around it. At that point, Yuki would be rebuilding a database badly. YAML remains the right choice for declarative inputs, not the source of truth for live concurrent state.

**Why PostgreSQL appears in this project:** the privileged migration feature specifically administers PostgreSQL target databases and must create constrained LOGIN roles, grant/revoke role membership, terminate sessions, inspect role attributes and apply SQL migrations. Those operations are PostgreSQL-specific. That does not imply that the controller's own attempt journal must also live in PostgreSQL.

## Recommended MVP storage direction (provisional)

For the first private, single-OCI-Linux-VPS pilot, prefer:
- YAML for checked-in and operator-reviewed configuration/policy templates.
- SQLite for local Yuki workflow/task state and, after a dedicated adapter and threat-model review, potentially the migration control-plane ledger.
- PostgreSQL only where Yuki is managing a PostgreSQL target or where a future multi-host control-plane deployment needs a shared transactional database.

This is a provisional recommendation, not a claim that the migration control-plane SQLite adapter already exists or is production-ready. The existing `WorkflowRunStore` is a useful implementation reference, not a drop-in credential ledger. A dedicated migration ledger still needs its own schema, restrictive file permissions, encrypted volume, transactional CAS, keyset recovery queries, idempotency constraints, backup/restore tests, crash-recovery tests, and production composition that cannot silently fall back to memory.

SQLite is appropriate only while one VPS owns the control plane and all processes access a local disk file. Do not put the database on NFS or a shared network filesystem, and do not let multiple VPS instances independently write it. If Yuki becomes multi-host, needs active-active controllers, or must survive loss/compromise of the VPS independently, move the control-plane store to a separately operated PostgreSQL service or another transactional managed database. If the PostgreSQL target itself is local to the same VPS, a local SQLite file is a separate database but not an independent host/security failure domain; document and accept that risk or use a separately hosted control-plane service.

## Decision

Do not directly use either existing component as the migration credential control-plane implementation.

Define a narrow provider-neutral persistence port for migration attempts and leases. Keep the concrete backend undecided until the pilot's database topology, availability requirements, backup/restore design, and secret-manager choice are approved.

The port must support:

1. Create an attempt idempotently using a unique attempt ID and immutable request digest. Enforce uniqueness for both attempt ID and idempotency key in the same transaction. A replay is accepted only when request digest and target identity both match; a different digest or target conflicts. The canonical request digest must cover target identity and immutable release/artifact/manifest authorization inputs, while the store independently compares target identity rather than trusting the digest alone.
2. Read an attempt by ID and scan bounded pages of expired/non-terminal attempts using keyset pagination ordered by `(updated_at, attempt_id)`. Apply the strict cursor predicate and page limit in the same query; define a stable cutoff for each scan and ensure failed early records cannot starve later attempts.
3. Transition state using an expected version (compare-and-swap); stale writes must fail closed. The state update and corresponding durable version increment must be one atomic database operation.
4. Acquire, renew, and release target-scoped leases with monotonically increasing fencing generations and bounded expiry.
5. Persist only secret-manager reference identifiers, never passwords, DSNs, tokens, or secret payloads.
6. Append audit events with stable event IDs and idempotent insertion.
7. Preserve enough state to recover after process restart and controller/reconciler separation.
8. Expose health and readiness failures without treating unavailable storage as successful cleanup.

The controller and reconciler must use separately authorized identities. The reconciler must be able to discover and retire abandoned attempts without relying on process memory from the controller that created them.

## Why the current stores are not drop-in replacements

### WorkflowRunStore

The current implementation is SQLite-backed and stores workflow definitions/results and worker lifecycle metadata. Its documented contract explicitly states that it is a persistence primitive, not a scheduler, and that expired work is not automatically replayed. Recovery is caller-invoked and ambiguous external side effects are marked interrupted rather than silently retried.

That is not equivalent to a security reconciler that independently scans migration attempts and proves that role membership, login capability, active sessions, and secret references have been retired. Reusing its schema or state transitions without a dedicated threat model would conflate task execution state with privileged credential lifecycle state.

It may inform implementation patterns (bounded values, idempotency, CAS, leases, sanitized errors), but any future adapter must prove multi-process contention behavior, durable backup/restore, fencing correctness, and independent reconciler operation on the selected deployment topology.

### SQLiteOAuthCredentialVault

The vault is specifically an OAuth credential store. It encrypts token values only through an injected trusted AEAD cipher, uses a separate host-managed HMAC key for refresh-token fingerprints, and stores encrypted values in SQLite. Its documentation explicitly excludes production key custody, KMS integration, key rotation jobs, and external provider revocation.

It must not be repurposed as a generic secret manager or assumed to provide PostgreSQL role lifecycle operations. A future secret-manager adapter must use the selected host/cloud secret service's native versioning, access controls, revocation semantics, audit trail, and independent verification.

## Required interfaces

Keep the application layer dependent on narrow protocols, not SDKs or provider-specific classes:

- `MigrationAttemptStore`: create/read/CAS transition/list expired attempts.
- `TargetLeaseStore`: acquire/renew/release fenced target leases.
- `MigrationSecretManager`: issue, resolve for one-shot execution, revoke, and independently verify a credential reference.
- `MigrationIdentityAdmin`: create constrained temporary LOGIN, grant/revoke only the intended SET membership, terminate sessions, disable login, invalidate credentials, and inspect effective role state.
- `MigrationRunner`: execute an immutable artifact and manifest against an explicitly identified target; return sanitized outcome metadata. Each invocation carries the target fencing generation, which must be checked atomically at the protected execution boundary.

A lease store rejecting stale renewals is not enough: a process may pause after acquiring a lease and resume after a newer owner has taken over. The target-side runner/adapter must persist the highest accepted generation per target and reject lower generations before applying work. Replays remain idempotent for the same attempt and manifest digest; attempt-ID reuse with a different digest conflicts.
- `MigrationAuditSink`: append idempotent, secret-free events and expose independent durability/health checks.

Do not make a single interface capable of returning secret material to general orchestration code. Secret resolution should be scoped to the one-shot runner boundary and must not be serialized into attempt state, audit payloads, workflow results, or logs.

## Credential saga store contract status

The provider-neutral credential saga domain and `CredentialProvisioningStore` protocol are defined. The test-only `InMemoryCredentialProvisioningStore` exercises immutable attempt/target/LOGIN binding, fresh-reservation-only creation, idempotent same-binding create, transactional-style version CAS, concurrent stale-writer rejection, and injected failure without partial mutation.

This fake is not a durable adapter and must never be used by production composition. The protocol now also requires a bounded keyset recovery scan ordered by `(updated_at, attempt_id)` so repeated failures on an early page cannot starve later attempts; the fake tests timestamp ties, cursor validation, and complete multi-page traversal. A PostgreSQL implementation and its schema migration are intentionally deferred until the control-plane backend is explicitly selected and its isolation boundary is approved. Do not add the ledger to the existing application migration manifest: doing so would silently choose the application database as the control plane. Once a backend is selected, the durable implementation must enforce unique attempt and cluster-global LOGIN reservations, row-level transactional CAS, immutable secret reference/version, indexed recovery of nonterminal states, and independent backup/restore and failure-recovery evidence. The fake's lock is only a deterministic test model; it does not prove database isolation or multi-process correctness.

## Backend selection gate

Before selecting a concrete attempt store, document and test:

- Whether control-plane state is in a distinct database/service from the target application database.
- Failure behavior when either store is unavailable.
- Concurrent controller/reconciler behavior and lease fencing under process crashes.
- Backup, restore, retention, encryption, and disaster-recovery procedures.
- Least-privilege identities and network paths from the private Linux VPS.
- How the reconciler remains independently supervised and alerted.

A dedicated SQLite ledger on the single pilot VPS is the provisional MVP recommendation, subject to the security and recovery gates above. A separately hosted PostgreSQL control-plane service is the scale-out option, not a prerequisite today. Reusing the application database, placing SQLite on shared storage, or selecting another managed database requires explicit review. Do not select an OCI region or provision resources in this ADR.

## Security invariants

- No production credential bytes in GitHub, source control, workflow outputs, process arguments, logs, audit events, or model context.
- No migration attempt is marked complete until independent role/session/secret retirement verification succeeds.
- No new migration attempt for a target while an earlier attempt has unresolved cleanup, unless an explicitly reviewed recovery policy authorizes it.
- Storage, secret-manager, identity-admin, or audit unavailability blocks promotion; it never implies success.
- Test fakes and in-memory stores are test-only and cannot be selected by production configuration.
- No silent fallback from a failed production adapter to SQLite, memory, or a no-op implementation.

## Implementation sequence

1. Add and test provider-neutral domain types, transition validation, and narrow protocols.
2. Add deterministic fake adapters and crash/failure-injection tests.
3. Select and implement the durable control-plane store and fenced lease adapter.
4. Select and implement the secret-manager and PostgreSQL identity-admin adapters.
5. Implement controller and independently supervised reconciler.
6. Run disposable PostgreSQL 16 integration tests and provider-specific integration tests.
7. Require independent review, CI success, backup/restore evidence, and explicit release approval before any pilot deployment.

This decision narrows the architecture. It does not claim that any controller, adapter, reconciler, secret-manager integration, or cloud resource currently exists.
