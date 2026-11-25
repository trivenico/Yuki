# PostgreSQL Role Boundaries and Privilege Inventory

## Status

This document records the privilege surface found in the PostgreSQL repository implementations at the time this branch was created. It is an engineering inventory, **not an executable GRANT script**. Do not provision production roles from guessed privileges. The exact grants must be generated only after repository ownership, connection routing, migrations, and integration tests are aligned.

## Security objectives

- Keep schema ownership and DDL in a release-only migration identity.
- Give API, worker, and operator processes distinct login identities and secret scopes.
- Do not give runtime identities superuser, database-owner, schema-owner, or general DDL privileges.
- Keep authorization audit writes constrained and make independent checkpoint publication a separate trust boundary.
- Ensure a compromised worker cannot modify conversation history or workload signing keys unless a documented worker task requires it.
- Keep all runtime connections private and TLS-verified; credentials come from the deployment secret manager, never source control.

## Verified repository operation inventory

The following operation classes were read from the PostgreSQL repository implementations. Table names are unqualified in current SQL and therefore resolve through the configured search path; production deployment should pin a trusted schema/search_path and avoid writable schemas earlier in that path.

| Repository | Tables observed | Operations observed | Notes |
|---|---|---|---|
| `PostgresConversationStore` | `conversations`, `conversation_messages`, `conversation_generation_leases`, `conversation_generation_runs` | Runtime DDL (CREATE TABLE/INDEX); inherited store CRUD | **Blocking issue:** constructor calls `_initialize_schema()` using the runtime connection. Move schema creation to migrations before granting a non-DDL API role. These four tables were not present in the current 0001–0015 security migration inventory. |
| `PostgresAuthorizationAuditLog` | `authorization_audit_state`, `authorization_audit_events` | SELECT, INSERT events, UPDATE singleton state; transactional advisory locks; bounded reads and chain verification | API audit writer needs append/update plus read-back for integrity checks. SQLite-history import is a separate administrative operation and must not be available to the ordinary API identity. |
| `PostgresDelegationAuthority` | `workload_delegations`, `workload_delegation_events` | INSERT grants/events; SELECT grants; UPDATE revocation; readiness SELECT | Assign to the process that issues and validates delegation. Do not automatically grant these rights to every worker. |
| `PostgresSecurityAlertOutbox` | `security_alert_outbox` | INSERT, SELECT, UPDATE; row locking and claim/lease transitions | Worker delivery needs claim/update/read; incident-to-outbox synchronization is operator/security-runtime work. Split identities or use narrow stored procedures if the same table's write surface must be restricted further. |
| `PostgresSecurityEventIngestionCursors` | `security_ingestion_cursors` | INSERT, SELECT, UPDATE | Worker-owned cursor and lease state. |
| `PostgresSecurityEventMonitor` | `security_monitor_signals`, `security_monitor_incidents`, `security_monitor_incident_history` | INSERT signals; SELECT counts/incidents; INSERT/UPSERT incidents; UPDATE incident status; INSERT history | Ingestion and operator workflows currently share a repository surface. Consider separate read/operator and ingestion connections if least privilege requires it. |
| `PostgresSecurityLeaseStore` | `security_work_leases` | INSERT/UPSERT, SELECT, UPDATE | Worker coordination state. |
| `PostgresWorkloadKeyRegistry` | `workload_keys`, `workload_key_audit` | INSERT keys/audit; SELECT keys/audit; UPDATE key lifecycle; row/advisory locks | Key lifecycle mutation is privileged administration. Ordinary request verification should be read-only. Split registry administration from verification before assigning runtime grants. |
| Migration executor | `public.yuki_schema_migrations` plus migration-owned objects | CREATE/ALTER and ledger SELECT/INSERT | Dedicated release identity only; never API/worker. |

## Current composition gap

`create_production_security_runtime()` accepts one `database_url` and passes it to authorization audit, workload key registry, ingestion cursors, alert outbox, and work leases. The monitor and factories also receive a copied configuration derived from the same configuration object. This is not sufficient for independently authenticated API and worker database roles.

Before role enforcement, change the deployment-owned bootstrap contract to accept explicit, independently validated connection identities per responsibility (at minimum API, worker, and migrator; operator/key-administration may require a fourth identity). Do not implement role separation by embedding passwords or by changing the global connection URL implicitly.

## Required remediation before a GRANT file is approved

1. Add the four conversation tables and indexes to the ordered migration manifest. Remove `_initialize_schema()` DDL from `PostgresConversationStore`; readiness should perform read-only schema checks only.
2. Add explicit connection configuration to each repository factory. The API process must not receive the worker or migrator secret; the worker process must not receive the API audit or key-administration secret unless its assigned task requires it.
3. Split workload-key verification (read-only) from key lifecycle administration (write) and isolate SQLite audit-history import behind a one-time migration/admin command.
4. Decide whether monitor incident updates and alert synchronization are worker, operator, or dedicated security-service responsibilities; encode those assignments in the bootstrap contract.
5. Review every SQL statement and migration constraint/index/sequence for exact privileges, including identity/serial sequence access, foreign-key effects, row-level security, schema ownership, and default privileges.
6. Add integration tests that connect using the proposed non-owner roles and prove both required operations and forbidden operations (DDL, cross-domain reads/writes, audit tampering, key mutation, and migration execution) behave as intended.
7. Generate an idempotent role provisioning script only after steps 1–6. Role groups should be NOLOGIN; login principals should be provisioned by the deployment secret manager. Never store passwords in this repository.
8. Document rotation, revocation, TLS, private networking, backup/restore, and emergency migration access.

## Release gate

No production PostgreSQL cutover is approved by this inventory. The migration executor PR and this role-boundary work must pass CI, receive review, and be applied only to a dedicated staging database before any production migration. The API and worker must start successfully with their restricted identities before the private VPS pilot can advance.
