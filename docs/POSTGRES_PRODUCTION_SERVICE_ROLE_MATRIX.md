# PostgreSQL Production Service Role Matrix

Status: design and review artifact; **not an executable grant script**.

This matrix records the intended database authority of each production
connection identity. It is derived from the PostgreSQL runtime composition,
repository call paths, migration inventory, and the audit-reader grant matrix.
It does not prove that a deployed PostgreSQL cluster currently has these roles
or grants. Do not provision production credentials from this document alone.

## Principal model

Use one NOLOGIN group role per responsibility and a distinct LOGIN principal
for each runtime connection. Grant each LOGIN principal membership in exactly
one corresponding group role. Avoid shared LOGIN principals, role inheritance
between runtime groups, superuser, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS,
and ownership by application roles.

| Connection identity | Intended group role | Allowed responsibility | Explicitly excluded |
| --- | --- | --- | --- |
| Migration executor | `yuki_migrate_role` | Apply reviewed DDL migrations and maintain the migration ledger during a controlled release | API/worker runtime, normal application traffic |
| Authorization audit writer | `yuki_authorization_audit_writer` | Verify chain state and append authorization decisions transactionally | Schema DDL, key lifecycle, monitor/outbox administration |
| Authorization audit reader | `yuki_authorization_audit_reader` | Read minimized authorization audit pages and readiness state | All writes, key registry, ledger |
| Workload-key registry | `yuki_workload_key_registry` | Resolve public keys and perform authorized key lifecycle operations | Schema DDL, audit-ingestion cursor management |
| Workload-key audit reader | `yuki_workload_key_audit_reader` | Read bounded workload-key audit projections | Key resolution, key mutation, all writes |
| Security monitor | `yuki_security_monitor` | Record signals; create/update incidents; read incidents; append incident history | Migration ledger, unrelated workflow data |
| Ingestion cursors | `yuki_ingestion_cursors` | Claim/renew/complete/fail fenced source checkpoints | Audit source writes, monitor/outbox mutation |
| Alert outbox | `yuki_alert_outbox` | Enqueue, claim, retry, acknowledge, list authorized projections and read aggregate health | Migration ledger, audit source mutation, unrelated workflow data |
| Security work leases | `yuki_work_leases` | Claim, renew and release fenced security-pipeline leases | Other security tables, DDL, ledger |

The production security runtime currently accepts eight distinct connection
identities. The migration executor is a separate short-lived deployment
identity. The production worker also has separate workflow and delegation
database credentials; those are separate from the eight security-runtime
identities and must be provisioned as distinct roles when the worker is enabled.

## Relation-level responsibility map

Names below refer to relations in the checked-in migrations. Operations are
the intended repository operations; they are not a recommendation to grant
table-wide privileges where narrower column grants suffice.

| Principal | Relations | Required operations |
| --- | --- | --- |
| Migration executor | `public.yuki_schema_migrations` and all migration-created objects | CREATE/ALTER/INDEX/constraints/triggers and ledger SELECT/INSERT/UPDATE as required by the migration runner. DDL ownership stays with this controlled identity or a tightly controlled NOLOGIN owner role. |
| Authorization audit writer | `authorization_audit_events`, `authorization_audit_state` | Read chain/head; append event; update singleton head. Transaction-scoped advisory lock is a PostgreSQL function call, not a table grant. |
| Authorization audit reader | `authorization_audit_events`, `authorization_audit_state` | SELECT only on the exact projection columns in [POSTGRES_AUDIT_READER_GRANT_MATRIX.md](POSTGRES_AUDIT_READER_GRANT_MATRIX.md). |
| Workload-key registry | `workload_keys`, `workload_key_audit` | Resolve/read keys; register keys; append lifecycle audit rows; update permitted lifecycle state/timestamps. Exact column and sequence requirements remain to be validated. |
| Workload-key audit reader | `workload_key_audit` | SELECT only on the exact projection columns in [POSTGRES_AUDIT_READER_GRANT_MATRIX.md](POSTGRES_AUDIT_READER_GRANT_MATRIX.md). No access to `workload_keys`. |
| Security monitor | `security_monitor_signals`, `security_monitor_incidents`, `security_monitor_incident_history` | INSERT signals; read signals for aggregation; insert/upsert and read incidents; update incident status; append history; read readiness projections. |
| Ingestion cursors | `security_ingestion_cursors` | Read, insert, and update lease owner/token/epoch/expiry and checkpoint/error fields needed by claim, renew, complete, fail and snapshot. No DELETE/TRUNCATE or source-identity mutation. |
| Alert outbox | `security_alert_outbox` | Insert alerts; claim and read delivery payload; update attempts/lease/retry/delivery/dead-letter fields; read minimized operator list and aggregate health. Operator authorization remains enforced by trusted application policy as well as database access. |
| Security work leases | `security_work_leases` | Insert and read lease rows; update owner/token/epoch/expiry fields for claim/renew/release. No DELETE/TRUNCATE or unrelated table access. |

## API and worker application roles

The security runtime's eight identities do not replace application storage
identities. Keep these additional principals separate:

- API conversation repository: only the conversation, message and generation
  lease/run operations actually used by the API repository. It must not have
  access to migration ledger, workload-key administration, monitor, outbox,
  or security lease tables unless a reviewed API call path explicitly requires
  a narrowly scoped read.
- Worker workflow repository: only workflow-run/task state operations used by
  the worker.
- Worker delegation authority: only delegation read/issue/revoke operations
  required by its repository.
- Migration executor: never supplied to API or worker process environments.

Do not collapse workflow and delegation credentials into one login merely
because both stores target the same logical database. Database endpoint
identity is not authorization identity.

## Global schema and role constraints

1. Revoke schema CREATE from PUBLIC and every runtime LOGIN/group role.
2. Restrict CREATE, ALTER, DROP, ownership and migration-ledger privileges to
   the controlled migration identity/owner role.
3. Runtime LOGIN roles must not own application tables, schemas, sequences,
   functions or triggers.
4. Revoke PUBLIC table, sequence and function privileges unless a specific
   reviewed operation requires them.
5. Use exact column grants where practical; avoid blanket table-level UPDATE
   when only a subset of columns is mutable.
6. Do not grant BYPASSRLS or role-management attributes to runtime principals.
7. Review SECURITY DEFINER functions and trigger functions separately. A table
   grant alone does not establish a safe function execution boundary.
8. Validate effective privileges after role membership and PUBLIC inheritance,
   not merely direct grants.
9. Keep connection strings in the deployment secret manager/systemd credentials;
   never store them in this document, notebooks, workflow logs or source control.

## Required PostgreSQL integration evidence

For every role, test using a real LOGIN principal connected as that role, not
only a mocked repository or SET ROLE from a superuser session.

- Positive tests: each repository readiness check and every supported
  operation succeeds using only its intended role.
- Negative tests: cross-table SELECT, INSERT, UPDATE, DELETE, TRUNCATE, DDL,
  schema CREATE, migration-ledger access, role escalation and unauthorized
  sequence/function access fail.
- Check generated identity values and sequence privileges where migrations
  use identity/serial columns.
- Verify foreign-key checks, triggers, advisory locks, row locks and
  `SELECT ... FOR UPDATE` behavior under the exact grants.
- Verify no runtime role owns an object and no inherited PUBLIC/table-level
  grant widens its access.
- Verify connection authentication maps each DSN to the intended LOGIN role.
- Exercise API, worker and security-runtime startup/readiness with the actual
  least-privilege principals.
- Run the full migration suite using only the dedicated migration identity;
  prove that runtime identities cannot create or alter schema objects.

## Rollout gate

This document is not sufficient to authorize production provisioning. Before
the Oracle Cloud private Linux pilot:

1. Derive exact column and sequence grants from every SQL statement in each
   repository.
2. Implement grants in a reviewed deployment provisioning procedure, not in
   application startup.
3. Add disposable PostgreSQL 16 role-isolation integration tests for every
   identity above.
4. Verify all allowed and denied operations with the actual LOGIN roles.
5. Review the resulting grants with `information_schema`,
   `pg_catalog` privilege functions and ownership queries.
6. Record the grant manifest, migration version, test run and release commit
   in the deployment evidence.
7. Keep the pilot private until the API identity/policy/audit-anchor bootstrap,
   backups, restore and rollback gates are independently satisfied.

No PostgreSQL roles, passwords, grants, cloud resources or database changes
are created by this document.
