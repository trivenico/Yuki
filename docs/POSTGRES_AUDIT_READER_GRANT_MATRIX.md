# PostgreSQL Audit Reader Grant Matrix

This matrix is derived from the current reader implementation and migrations
`0004_workload_key_registry.sql` and `0006_authorization_audit.sql`. It is a
review artifact, not an executable grant script.

## Read-only principals

| Principal purpose | Required relation | Required columns | Operations |
| --- | --- | --- | --- |
| Authorization audit ingestion | `authorization_audit_events` | `event_id, occurred_at, principal_hash, reason_code, allowed` | SELECT |
| Authorization audit readiness | `authorization_audit_state` | `singleton` | SELECT |
| Workload-key audit ingestion | `workload_key_audit` | `event_id, administrator, issuer, key_id, action, previous_state, new_state, occurred_at` | SELECT |

The reader implementations issue only SELECT statements and execute each query after
`SET TRANSACTION READ ONLY`. They do not need access to `workload_keys`, authorization
event append operations, audit state updates, or key lifecycle operations.

## Writer/admin principals remain separate

The authorization audit writer needs a different grant profile. Its current code
reads the complete authorization event chain and singleton state, inserts audit
events, and updates the singleton head within a transaction. It also uses a
transaction-scoped advisory lock. The migration identity alone owns DDL and schema
migration ledger operations.

The workload-key lifecycle repository reads key rows and audit history, inserts
key registrations and audit events, and updates key state/timestamps. It uses
transaction-scoped advisory locks. It must not be used as the ingestion reader.

Do not infer exact sequence privileges or broader table grants from this summary.
Validate PostgreSQL identity-column behavior, schema usage, relation/column grants,
trigger execution, readiness queries, and advisory-lock behavior in a disposable
PostgreSQL instance before authoring executable grants.

## Required negative tests before production

1. Authorization reader can fetch a bounded event page and readiness state.
2. Authorization reader cannot INSERT, UPDATE, DELETE, TRUNCATE, or change schema.
3. Workload-key audit reader can fetch a bounded audit page.
4. Workload-key audit reader cannot read `workload_keys` or mutate any table.
5. Neither reader can execute DDL, alter roles, assume writer roles, or access the
   migration ledger.
6. Writer/admin roles retain only the operations required by their repository SQL.
7. Revoke inherited PUBLIC privileges and test effective privileges, not only direct
   grants.
8. Verify the actual runtime DSNs authenticate as the intended principals.

Use separate LOGIN principals and NOLOGIN group roles. Do not put credentials or
`GRANT` statements into this document or application source. Grants must be applied
by the short-lived migration/deployment identity after review.

## Scope and remaining work

This matrix covers only the two audit-reader paths. It does not define grants for
security monitor, ingestion cursors, alert outbox, work leases, API, worker, or the
migration executor. Those repositories require their own SQL-derived matrices and
positive/negative integration tests before database-role rollout.
