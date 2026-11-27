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
| Alert outbox delivery | `yuki_alert_outbox` | Enqueue, claim, read delivery payload, retry and acknowledge with lease fencing | Operator-only access, migration ledger, audit source mutation |
| Alert operator reader | `yuki_alert_operator_reader` | SELECT only on minimized alert-list and aggregate-health columns | Payload, lease owner/token, all writes, migration ledger |
| Security work leases | `yuki_work_leases` | Claim, renew and release fenced security-pipeline leases | Other security tables, DDL, ledger |

The production security runtime currently accepts nine distinct connection
identities, including separate alert delivery and operator-reader principals. The migration executor is a separate short-lived deployment
identity. The production worker also has separate workflow and delegation
database credentials; those are separate from the nine security-runtime
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
| Alert outbox delivery | `security_alert_outbox` | Insert alerts; claim and read delivery payload; update attempts/lease/retry/delivery/dead-letter fields. |
| Alert operator reader | `security_alert_outbox` | SELECT only on `alert_id, incident_id, state, attempts, created_at, available_at, last_error_code, delivered_at, lease_until` for minimized listing and aggregate health. |
| Security work leases | `security_work_leases` | Insert and read lease rows; update owner/token/epoch/expiry fields for claim/renew/release. No DELETE/TRUNCATE or unrelated table access. |

## API and worker application roles

The security runtime's nine identities do not replace application storage
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


## Code-derived SQL review (repository inspection)

The following findings were checked against the current PostgreSQL repository
implementations. They describe SQL operations observed in code, not grants
already installed or validated against a live PostgreSQL server.

| Repository | Relations and observed SQL behavior | Permission implications / unresolved proof |
| --- | --- | --- |
| `core/postgres_authorization_audit.py` | Reads `authorization_audit_state`; selects the complete event-chain projection from `authorization_audit_events`; inserts explicit `event_id` and hash-chain columns; updates singleton `last_event_id/head_hash`; locks state row with `FOR UPDATE`; takes transaction advisory lock. | Writer needs SELECT on the chain columns and state columns, INSERT on event columns, UPDATE on state columns, and row-lock capability. No identity sequence is used for event IDs because the repository allocates IDs from the locked state row. Verify the exact minimum SELECT/UPDATE column grants with PostgreSQL. |
| `core/postgres_security_audit_readers.py` | Authorization reader selects `event_id, occurred_at, principal_hash, reason_code, allowed` and singleton state; workload-key reader selects the documented audit projection. Each read is inside `SET TRANSACTION READ ONLY`. | SELECT-only grants on exact projections; read-only transaction is defense in depth, not a replacement for database grants. |
| `core/postgres_workload_key_registry.py` | Reads and writes `workload_keys`; inserts into and reads `workload_key_audit`; updates key state/timestamp; uses `SELECT ... FOR UPDATE`; takes issuer and audit-order transaction advisory locks. | Registry needs narrowly scoped SELECT/INSERT/UPDATE on both relations. Audit event ID is generated by the database identity column, so inspect and test the backing sequence's effective privileges rather than assuming table INSERT is sufficient. No DELETE path was observed in this repository. |
| `core/postgres_security_event_monitor.py` | Inserts `security_monitor_signals`; reads signals for threshold aggregation; inserts/upserts and reads `security_monitor_incidents`; reads incidents; locks incident rows; updates status/operator hash; inserts `security_monitor_incident_history`; takes advisory transaction locks. | Monitor needs SELECT/INSERT on signals; SELECT/INSERT/UPDATE on incidents; INSERT on history; sequence privileges for generated identity columns must be tested. Exact column grants and UPSERT conflict-update requirements need PostgreSQL integration proof. |
| `core/postgres_security_event_ingestion.py` | Inserts cursor source rows with ON CONFLICT DO NOTHING; updates lease owner/token/epoch/expiry and checkpoint/error fields; reads cursor snapshots and readiness projections. | Cursor role needs SELECT/INSERT/UPDATE on the cursor relation. The implementation has no DELETE path. ON CONFLICT and UPDATE predicates may require SELECT rights on referenced columns; prove effective minimum grants with the real LOGIN role. |
| `core/postgres_security_alert_outbox.py` | Inserts alert payloads; claims using a CTE with row locking and updates; moves exhausted deliveries to dead state; reads payload and lease fields for delivery; locks rows during completion; updates delivery state; operator listing selects a minimized projection; health query aggregates state/time columns. | Delivery worker needs SELECT on payload and claim predicates plus INSERT/UPDATE on the operational columns. Operator projection does not select payload, but the same repository/connection identity also performs delivery and therefore can read it. If operator and delivery processes must have different data visibility, split the repository/credentials or expose a narrowly scoped database view/function; application projection alone does not prevent direct SQL access. |
| `core/postgres_security_lease_store.py` | Inserts and upserts `security_work_leases`; updates expiry; predicates on owner/token/epoch; returns lease projection. | Requires SELECT/INSERT/UPDATE on lease relation. No DELETE path observed. Validate ON CONFLICT update privileges and row-level behavior under exact column grants. |
| `core/postgres_conversation_store.py` | Verifies the four expected public relations with `to_regclass`, then delegates data operations through the shared `ConversationStore` SQL implementation. | This adapter alone is not enough to finalize a grant list. Review the inherited/shared SQL operations and all PostgreSQL integration tests before specifying conversation table columns. It performs no runtime DDL. |

### Important boundary discovered: alert operator versus delivery access

The production runtime now uses a dedicated `PostgresSecurityAlertOperatorReader`
with its own `alert_operator_reader` DSN. The reader exposes only list and health
queries and has no delivery methods. Its intended LOGIN role receives SELECT on
`alert_id, incident_id, state, attempts, created_at, available_at,
last_error_code, delivered_at, lease_until`; it must not receive SELECT on
`payload`, `lease_owner`, or `lease_token`, nor any write privilege. The delivery
repository retains its separate payload-capable identity. The new disposable
PostgreSQL integration test proves these denials using a real LOGIN principal.
This is repository and test evidence; deployment provisioning must still create
the distinct credentials and exact grants before the boundary exists in a live cluster.

### API conversation-store identity

The API conversation repository is a separate LOGIN principal from all
security-runtime and worker principals. Its current repository operations use
four relations: `conversations`, `conversation_messages`,
`conversation_generation_leases`, and `conversation_generation_runs`.
The intended grant contract is column-scoped SELECT/INSERT/UPDATE on the
columns exercised by the shared `ConversationStore` implementation, plus
table-level DELETE only on `conversation_generation_leases` because lease
release is a DELETE operation. The API principal must have no access to the
migration ledger, security monitor, alert outbox, workload-key registry,
security work leases, workflow runs, or delegation tables. PR #1334 adds a
disposable PostgreSQL LOGIN-role test that exercises the repository lifecycle
and inspects the effective column/table privileges. The runtime DSN must be
provisioned as a distinct secret; this test does not provision it.

### Worker workflow identity

The worker's `PostgresWorkflowRunStore` uses `workflow_runs` and
`task_events`. The intended LOGIN role receives column-scoped SELECT on the
repository's workflow and event projections, INSERT on workflow creation and
event fields, and UPDATE only on lifecycle, approval, result, version, and
lease-fencing fields. The `task_events_event_id_seq` sequence receives USAGE
only. It receives no DELETE/TRUNCATE, DDL, schema CREATE, delegation,
security-domain, or migration-ledger access. Workflow ownership fields
(`tenant_id`, `owner_principal_id`, `ownership_status`) are readable and
insertable but not updateable; the migration's immutability trigger remains an
additional database guard. PR #1336 adds a disposable PostgreSQL LOGIN-role
test that executes create, idempotent reads, approval, claim, append, terminal
transition, and task-event listing using only these grants, then verifies
cross-domain denials and effective column/sequence privileges. This remains
test evidence, not deployed grants.

### Worker delegation identity

The worker's `PostgresDelegationAuthority` uses only
`workload_delegations` and `workload_delegation_events`. Its intended LOGIN
role has column-scoped SELECT across delegation rows, INSERT on grant fields,
and UPDATE only on `revoked_at`. The event journal has SELECT only on the
readiness projection (`event_id,event_type,delegation_id`) and INSERT on event
fields. The generated `event_id` requires USAGE on
`workload_delegation_events_event_id_seq`; no sequence UPDATE, table-level
DML, DELETE, TRUNCATE, DDL, workflow, security, or migration-ledger access is
intended. PR #1335 adds a disposable PostgreSQL LOGIN-role test for issue,
verify, revoke, chain-scope/expiry restrictions, append-only event behavior,
and effective column/sequence privileges. These are test grants, not yet
deployment provisioning.

### Grant validation still required

- Test sequence access for each identity-backed event column using the exact
  PostgreSQL major version and migration definitions.
- Test INSERT ... ON CONFLICT under the exact SELECT/INSERT/UPDATE column
  grants; PostgreSQL checks privileges on both insert and conflict-update
  paths.
- Test SELECT ... FOR UPDATE and advisory lock calls using the real LOGIN role.
- Confirm trigger functions, foreign-key checks and generated defaults do not
  require unreviewed privileges.
- For conversation storage, derive privileges from the shared
  `ConversationStore` methods used by `PostgresConversationStore`; do not
  infer them from the adapter's schema check alone.

## Role validation inventory

This inventory distinguishes repository design from executable privilege evidence.
A linked PR means the test has been authored; it does **not** mean the test
passed. Check the PR's required GitHub Actions results and review the exact
head commit before promoting any row to verified.

| Responsibility | Runtime identity boundary | Disposable real-LOGIN test | Current evidence state |
| --- | --- | --- | --- |
| Migration executor | Dedicated short-lived migration identity; separate from all runtime principals | Not yet tracked in the role-isolation PR series | Grant/bootstrap test still required |
| Authorization audit writer | Dedicated append/chain-maintenance identity | New test in this branch | Authored; CI result must be confirmed |
| Authorization audit reader | Read-only projection identity | PR #1316 | Authored; CI result must be confirmed |
| Workload-key registry | Key lifecycle identity, separate from audit reader | PR #1332 | Authored; CI result must be confirmed |
| Workload-key audit reader | Read-only audit projection identity | PR #1316 | Authored; CI result must be confirmed |
| Security monitor | Signal/incident identity | PR #1331 | Authored; CI result must be confirmed |
| Ingestion cursors | Fenced source-checkpoint identity | PR #1317 | Authored; CI result must be confirmed |
| Alert outbox delivery | Payload-capable delivery identity | PR #1319 | Authored; CI result must be confirmed |
| Alert operator reader | Read-only minimized alert projection | PR #1333 | Authored; CI result must be confirmed |
| Security work leases | Fenced lease identity | PR #1318 | Authored; CI result must be confirmed |
| API conversation store | Conversation/message/generation identity | PR #1334 | Authored; CI result must be confirmed |
| Worker workflow store | Workflow/task-event identity | PR #1336 | Authored; CI result must be confirmed |
| Worker delegation authority | Delegation issue/verify/revoke identity | PR #1335 | Authored; CI result must be confirmed |

The next missing executable evidence is concentrated in the migration executor
and authorization audit writer identities. Before authoring deployment grants,
also consolidate all per-role tests into one disposable PostgreSQL 16
verification job that checks role attributes, membership, PUBLIC privileges,
object ownership, table/column privileges, sequence privileges, function
EXECUTE rights, and both allowed and denied repository operations. Do not
replace the focused per-repository tests: the consolidated job is an
independent cross-role regression gate.


### Authorization audit writer identity

The authorization audit writer uses a dedicated LOGIN principal. Its repository
needs SELECT across the hash-chain event projection and singleton chain state,
INSERT across the immutable event columns, and UPDATE only on
`authorization_audit_state.last_event_id` and `head_hash`. Event IDs are
allocated while holding the singleton state row lock; no event sequence is
used. The new `tests/test_postgres_authorization_audit_writer_role_isolation.py`
test exercises readiness, append, checkpoint, chain verification, append-only
denials, cross-domain denial, and exact column grants with a disposable real
LOGIN role. The role is not granted event UPDATE/DELETE, state INSERT/DELETE,
DDL, schema CREATE, or migration-ledger access. This is test evidence only;
no production role or grant has been provisioned.


### Authorization audit chain: integrity versus independent witnessing

The PostgreSQL writer's hash chain detects accidental or partial row changes when `verify_chain()` is run. The dedicated LOGIN role is append-only with respect to event rows, but it can insert a new event and update the singleton head because those operations are required for normal writes. A compromised writer credential can therefore append a syntactically valid but fabricated decision event and advance the local chain. Hash chaining and column grants do not establish that an event represents a genuine authorization decision.

The external checkpoint anchor protects already-published history against truncation or rewriting. It does not, by itself, establish the semantic authenticity of events appended after the last checkpoint. In particular, a publisher that blindly reads the same writer-controlled database and advances the anchor can permanently witness fabricated events.

Production requirements before treating the audit stream as independently trusted:

1. Run the checkpoint publisher under a separate host/service identity and separate secret boundary from the PostgreSQL writer. The writer must not possess anchor credentials or permission to publish checkpoints.
2. Do not automatically advance an external anchor solely because the local writer database reports a valid chain. The publisher must obtain independent authorization-decision evidence or a separately authenticated event batch from the authorization decision boundary before accepting a new head.
3. Bind accepted batches to monotonically increasing event IDs, the prior witnessed checkpoint, the exact event hashes, and an authenticated source identity. Reject gaps, duplicates, stale batches, and source identity changes.
4. Keep anchor publication monotonic and compare-and-set protected. Alert on missing publication intervals, publisher identity changes, unexpected event-rate changes, and disagreement between independently collected decision evidence and the database chain.
5. Test writer-credential compromise explicitly: demonstrate that the writer cannot publish or roll back the external anchor, and that a fabricated post-checkpoint event is not accepted by the independent witness.

Repository implementation already includes an HTTPS/mTLS-capable anchor client with ETag compare-and-set (`HttpsAuthorizationAuditAnchor`), a one-shot scheduler contract (`AuthorizationAuditCheckpointPublisher`), an independently stored SQLite anchor service with atomic monotonic CAS (`SQLiteAuditAnchorService`), an ASGI endpoint (`AuditAnchorASGI`), publisher enrollment/revocation, and short-lived EdDSA publisher authentication with replay prevention (`AuditAnchorCredentialAuthenticator`). These are code and test assets, not evidence of a separately deployed trust domain. Still missing are: a production deployment of the anchor service outside the Yuki database/host trust boundary; production identity/key/replay-state adapters and operational enrollment; an independent source of authenticated authorization-decision evidence (the current publisher can only validate the database chain); automated scheduling/health monitoring/alerting; and end-to-end writer-compromise tests against the deployed trust boundary. Do not claim that the PostgreSQL writer role alone makes authorization decisions non-repudiable or semantically authentic. This section is a production security gate, not proof that the independent witness is deployed.

## Signed authorization-decision receipt verifier

The repository now includes `core.authorization_decision_receipts.py`, an Ed25519 verifier adapter for the checkpoint publisher's `AuthorizationAuditEvidenceVerifier` contract. It fetches one independently retained receipt per unique decision digest, resolves the issuer's active public key through a host-supplied key resolver, verifies the detached Ed25519 signature over canonical JSON, and compares every security-relevant receipt field against the audit event (principal/tenant/agent, action/resource, task/delegation/approval, risk, allow/deny, policy revision, and reason). It rejects missing/duplicate receipts, malformed digests/timestamps, noncanonical boolean encodings, unknown/revoked keys, invalid signatures, and field mismatches.

This is a verifier implementation, not an issuer or a deployed independent service. The receipt source must be independently administered and retain the issuer's signed decision before/independently of the audit writer; the key resolver must enforce issuer-key activation, expiry, and revocation. The issuer private key must never be available to the Yuki audit writer, runtime database credentials, or model/agent execution. The Ed25519 implementation loads the optional `cryptography` dependency only when used; production deployments must install the `production-storage` extra. Runtime composition still accepts the verifier as a host dependency, and deployment must explicitly inject this adapter with separately operated source/key adapters. Unit tests exercise valid signatures, tampering, missing/duplicate receipts, unknown keys, and malformed metadata. No independent issuer, receipt store, key service, or production deployment is claimed here.

The checkpoint publisher now requires a host-supplied `AuthorizationAuditEvidenceVerifier` before advancing beyond the last witnessed event. It reads a separate, privileged `evidence_page` projection containing the complete event fields plus `previous_hash` and `event_hash`; the ordinary `event_page` remains privacy-minimized for monitoring consumers. It checks contiguous IDs, links each event to the preceding hash, and requires the final event hash to equal the proposed checkpoint before delegating independent authentication/correlation to `verify_batch(previous, current, events)`. Missing, incomplete, malformed, or rejected evidence fails closed without publishing. This is an explicit trust-boundary contract and regression-tested with rejecting/accepting verifiers and sequence-gap cases; no production verifier backed by an independent authorization-decision source exists yet. The event projection alone is not sufficient proof of authenticity, and a verifier that merely trusts those same database rows would violate the contract.


## Decision-boundary receipt recording

The central `AuthorizationGateway` now accepts an `AuthorizationDecisionEvidenceRecorder`. When configured, it calls the recorder and requires a non-empty opaque receipt ID before invoking the PostgreSQL audit writer. If independent receipt creation/retention fails, the gateway returns a deny and does not call the audit writer or allow the protected operation to proceed. Production runtime composition requires this recorder and injects it into the gateway; it is not an optional production setting. Unit/development gateway compositions may omit it, but that does not satisfy production security readiness.

The recorder is an idempotent host adapter contract keyed by the canonical SHA-256 decision hash; retries for an identical payload must return the same receipt ID, and conflicting payloads for an existing hash must be rejected. The gateway supplies this digest explicitly. The recorder is not a local signer. Its implementation must call the independent policy/identity issuer, persist the signed receipt to an independently administered source, and return only after durable retention. Receipt `decision_hash` must equal SHA-256 of the UTF-8 `decision_id` (the audit stores' existing digest convention); `authorization_decision_digest()` exposes that canonical calculation, and `authorization_audit_value_digest()` provides the same nullable-string digest convention for the other hashed request fields. Receipt `issued_at` must be timezone-aware and within five minutes of the audit event's `occurred_at`. The Ed25519 verifier later fetches receipts from that source. This preserves ordering (issuer + independent receipt retention → Yuki audit write → protected operation) without giving the audit writer access to issuer private keys. No production issuer or receipt-store adapter is included or claimed.


### Read-after-write verification at the authorization boundary

The module also provides `VerifiedAuthorizationDecisionEvidenceRecorder`, a composition wrapper for the production runtime's required recorder. It calls the external idempotent issuer/recorder, reads the receipt back from the independent source using the canonical decision hash, checks that the returned receipt ID matches, verifies the Ed25519 signature with the independent key resolver, and compares all request/decision fields before returning to `AuthorizationGateway`. Thus an allow decision cannot proceed to audit persistence or the protected operation merely because a remote POST returned success; the signed receipt must be independently readable and valid first. The wrapper uses the same digest conventions as the audit stores and enforces the five-minute issue-time correlation window. Production should inject this wrapper around its independently operated recorder/source/key adapters. It is still an integration composition, not a deployed issuer or receipt service.


Production composition is intentionally stricter than the general gateway interface: `create_production_security_runtime` accepts only the concrete `VerifiedAuthorizationDecisionEvidenceRecorder` wrapper, not an arbitrary object implementing the lower-level recorder protocol. This prevents a host from accidentally wiring a recorder that merely returns an opaque ID without independently reading back and validating the signed receipt. The wrapper itself still requires host-provided, independently administered recorder, receipt source, and issuer-key resolver adapters; those adapters and their deployment remain explicit production prerequisites.
