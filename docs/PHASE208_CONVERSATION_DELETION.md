# Phase 208 — authenticated conversation deletion

## Scope

Implement the persistence and authenticated HTTP API boundary for permanent
deletion of a single conversation. This phase is deliberately not declared
complete until every supported client and deployment backend has been integrated
and tested.

## Behavior

- `DELETE /v1/conversations/{conversation_id}` requires a valid bearer session.
- The owner identity is derived only from the authenticated principal.
- Unknown and foreign-owner IDs both return `404 not_found`.
- A conversation with an active API-managed generation returns
  `409 generation_active`; callers must wait for the generation to finish
  before retrying. Deletion checks the shared database generation-lease row
  inside the same write transaction, so separate API processes sharing that
  database cannot bypass the in-memory request registry.
- Lease expiry is not treated as proof that an execution has stopped. A stale
  lease therefore fails closed and requires a separately audited recovery
  procedure; automatic stale-lease reclamation is intentionally not implemented.
- The store uses a write transaction, owner-scoped lookup and owner-scoped
  delete. Foreign-key cascades delete messages, generation runs and leases.
- SQLite enables foreign keys on each connection. PostgreSQL inherits the same
  store method and translates the transaction start and placeholders.
- No soft-delete or recycle-bin copy is created by this endpoint.

## Acceptance evidence required

- Owner isolation and non-disclosure tests.
- Cascade tests for messages, generation runs and leases.
- Active-generation conflict tests.
- SQLite tests and PostgreSQL integration tests against a real supported
  PostgreSQL service, not only SQL translation unit tests.
- Concurrency/race tests for deletion against streaming and non-streaming
  generations.
- Client integration and end-to-end tests covering deletion UX and retry after
  an active generation ends.
- Audit/event behavior and explicit confirmation UX before destructive delete.
- Backup-retention and cross-store deletion behavior documented and tested.

## Known gaps — do not call production-ready yet

- This endpoint does not erase copied message content from logs, model-provider
  retention, external tool systems, backups, filesystem snapshots, exports,
  vector indexes, memory stores or other independent databases.
- There is no cross-store deletion coordinator or durable deletion ledger.
- There is no API-level audit event for destructive deletion yet.
- No rate limit, CSRF assessment for browser cookie deployments, or client UX
  integration is introduced in this change.
- PostgreSQL is covered by SQL translation tests only in this PR; a live
  PostgreSQL service integration run is still required.
- The database lease check covers service instances sharing the same database,
  but it is not yet a complete worker-fencing protocol. Existing model/tool
  execution writes do not carry a lease generation through every commit. A
  paused worker and lease-reclamation race must be addressed before automatic
  recovery or multi-host execution is production-ready.
- A crashed worker can leave a lease row behind. Deletion intentionally remains
  blocked until an operator-approved recovery path verifies the worker is no
  longer executing and clears the lease; there is no such recovery command yet.
- A process crash during deletion relies on database transaction atomicity;
  recovery and audit verification still need integration tests.

## Validation

Await exact-head GitHub Actions. No local tests, PostgreSQL service tests,
client E2E, security certification or production readiness are claimed.
