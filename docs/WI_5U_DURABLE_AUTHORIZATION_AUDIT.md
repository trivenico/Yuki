# WI-5U — Durable authorization audit infrastructure

## Finding

AuthorizationGateway required a callable audit writer and failed closed when it raised or returned literal false. However, any callable—including a no-op lambda—satisfied the contract. ProductionSecurityContext validated the gateway but did not validate the sink. The web production bootstrap could therefore accept a context that recorded no durable authorization events at all.

## Implementation

- Add SQLiteAuthorizationAuditLog as a dedicated durable authorization decision sink.
- Serialize concurrent writers across threads and processes with SQLite BEGIN IMMEDIATE and a bounded busy timeout.
- Store metadata only: principal, tenant, agent, resource, task, delegation, approval, decision ID, and policy revision are represented by SHA-256 digests; action, risk, allow/deny and stable reason code remain queryable.
- Append events in transaction order and hash-chain each event to its predecessor.
- Expose the configured gateway sink through a read-only property for trusted composition validation.
- Require the exact SQLiteAuthorizationAuditLog implementation in ProductionSecurityContext, preventing no-op or arbitrary callbacks from being accepted by strict production bootstrap.
- Add tests for persistence across instances, concurrent append serialization, chain verification/tampering, metadata minimization, and rejection of non-durable production sinks.

## Operational guidance and limitations

The SQLite database must be on a filesystem with reliable SQLite locking semantics and should be shared only by processes on the same host unless the storage layer explicitly guarantees SQLite locking. Do not put it on an arbitrary network filesystem. Back it up and monitor disk capacity and write failures. The gateway fails closed when the writer cannot commit.

The hash chain detects accidental or partial modification but is not independently tamper-proof against an attacker who can rewrite the database and recompute every hash. Production operations should periodically export and sign or externally anchor the latest head hash in a separately controlled system, and replicate audit data to a protected SIEM or append-only object store. This PR provides the local durable sink and head-hash accessor, not the external anchoring service or SIEM exporter.

This requirement applies to ProductionSecurityContext and strict production composition. Legacy local/development paths may still use callback-based sinks. Tests were added but not executed in this environment.
