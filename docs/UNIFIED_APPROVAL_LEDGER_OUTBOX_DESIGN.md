# Unified approval ledger and transactional outbox design

## Status

**Proposed architecture only. Not implemented and not enabled.**

The current approval coordinator uses separate SQLite databases for pending snapshots, audit events, and one-shot grants. A shared maintenance fence serializes cooperating processes, but it does not provide a transaction spanning those databases.

This document defines a migration target. It does not authorize schema migration, data movement, tool registration, or consequential execution.

## Goals and non-goals

Goals:
- Make internal approval state, audit event, and dispatch intent atomic within one SQLite transaction.
- Ensure a pending approval can be decided at most once.
- Ensure a dispatch intent can be claimed at most once concurrently.
- Preserve an explicit uncertain-outcome state after dispatch may have crossed the external side-effect boundary.
- Require live owner/session and policy validation immediately before dispatch.
- Make recovery inspect and reconcile state without silently replaying external actions.
- Keep the ledger private, bounded, backed up, and owner-controlled.

Non-goals:
- Claim exactly-once execution against arbitrary external systems.
- Treat a SQLite transaction as atomic with an HTTP API, shell process, filesystem mutation, or remote service.
- Automatically retry an uncertain action or restore consumed approvals.
- Enable arbitrary model-generated code or host tools.
- Replace independent audit checkpoints for high-assurance tamper evidence.

## Proposed single-database schema

Use one private-profile SQLite database, for example approval-ledger.sqlite3. Finalize schema version and SQLite compatibility before implementation.

### approval_requests

- request_id: random opaque primary key.
- owner_id_hash and session_id_hash: identity bindings; never store session bearer tokens.
- action_json: bounded canonical action snapshot.
- action_digest: digest of canonical action and exact target scope.
- state: constrained enum: pending, denied, approved, dispatch_pending, executing, completed, blocked, outcome_unknown, reconciled_executed, reconciled_not_executed, expired.
- created_at, expires_at, updated_at.
- version: integer for compare-and-swap state transitions.

### approval_events

- Monotonic event_id and request_id foreign key.
- Event type, actor hash, timestamp, bounded metadata.
- Previous-event hash and event hash.
- Unique event sequence enforced by the writer transaction.

The chain detects consistency errors; it is not authenticated proof against an owner who can rewrite the database. External signed checkpoints remain a separate control.

### dispatch_outbox

- dispatch_id: random opaque primary key.
- request_id: unique foreign key; one dispatch intent per approval request.
- action_digest, target_scope, and trusted handler identifier.
- state: pending, claimed, started, completed, blocked, outcome_unknown, or reconciled terminal state.
- claim_id_hash, claimed_at, claim_expires_at.
- attempt_count constrained to one for non-idempotent handlers unless a separately reviewed target-idempotency contract permits more.
- created_at and updated_at.

The outbox must not contain bearer capabilities, secrets, or unbounded raw tool output.

## Transaction protocol

### 1. Record a request

One BEGIN IMMEDIATE transaction:
1. Insert the bounded pending request with owner/session hashes and expiry.
2. Append the requested event.
3. Commit.

If either insert fails, roll back both. A request is never visible without its corresponding event.

### 2. Decide an approval

Owner confirmation occurs outside the write transaction so a human prompt does not hold a database lock.

After confirmation, revalidate the live owner session and policy. Then use one BEGIN IMMEDIATE transaction:
1. Read the request and verify pending state, version, expiry, actor hash, and session hash.
2. Compare the canonical action digest with the exact action shown for confirmation.
3. Change request state using a conditional update requiring the expected state and version.
4. If denied, append denied; if approved, append approved.
5. For approved actions, insert exactly one dispatch_outbox row and append dispatch_queued.
6. Commit.

A failed conditional update means the request was already consumed, expired, or changed. It must not be retried as a new approval.

### 3. Claim dispatch

A trusted dispatcher claims an outbox row in one BEGIN IMMEDIATE transaction:
1. Verify the request is approved and not expired.
2. Revalidate owner/session authorization and current policy revision.
3. Verify the registered handler and target scope against the approved digest.
4. Atomically change pending to claimed, assigning a fresh claim identifier and bounded lease.
5. Append dispatch_claimed.
6. Commit.

Do not hold a SQLite transaction open while calling an external service.

### 4. Cross the side-effect boundary

Immediately before invoking the handler:
1. Revalidate the live owner session and policy.
2. Verify the claim is current and the action digest still matches.
3. Record started durably in a short transaction.
4. Invoke only the exact registered handler with the exact approved arguments.

If the process crashes after started, Yuki cannot infer whether the target acted. Retain the operation as uncertain during recovery. Never infer non-execution from a missing completion event.

### 5. Record the result

After the handler returns, use a new transaction to append the terminal event and update request/outbox state.

If this transaction fails after the external target may have acted, preserve outcome_unknown. Do not automatically replay.

## External idempotency and the exactly-once limit

For a target that supports durable idempotency keys, derive a stable opaque key from dispatch_id and send it to the target. The target must guarantee repeated submissions with that key cannot duplicate the side effect, and expose a way to query the original result.

Only after that target contract is documented and tested may a handler-specific retry policy be considered.

For targets without idempotency and queryable outcomes:
- One dispatch attempt only.
- Any crash or ambiguous transport failure after dispatch starts becomes outcome_unknown.
- Require independent target inspection and owner reconciliation.
- A reconciled_not_executed record is a finding, not permission to reuse the old approval. A new action requires a new approval request.

## Recovery rules

Recovery is read-only by default:
- Pending requests with no outbox row are an invariant violation.
- Outbox rows with no matching approved request are an invariant violation.
- Expired pending requests may be reported; automatic deletion is not part of the first implementation.
- Expired claims are not automatically re-dispatched.
- Started operations without a terminal event remain uncertain.
- Duplicate dispatch rows are prevented by a unique constraint and reported if found in legacy data.
- Invalid event-chain state blocks dispatch and requires owner review.
- Recovery never executes handlers, recreates grants, or changes an uncertain result to not_executed without independent evidence.

Any future mutating reconciliation command must be separately owner-authenticated, evidence-bound, audited, and incapable of replaying the action.

## SQLite operational requirements

- Enable and verify foreign keys on every connection.
- Select and document journal mode; validate WAL/SHM backup and restore behavior.
- Use bounded busy timeouts and explicit transaction boundaries.
- Keep the database beneath the validated private profile and behind the canonical maintenance fence.
- Use SQLite online backup or a quiesced consistent snapshot; do not copy only the main database file while WAL is active.
- Validate integrity, foreign keys, schema version, event chain, and outbox invariants after restore.
- Apply bounded retention only where policy permits. Never prune unresolved operations or required audit history.
- Test the deployment filesystem and SQLite build on supported Linux ARM64 and CPU-only systems. The ledger does not require a GPU or local model.

## Migration plan — not authorized yet

1. Trace every production constructor and writer for the current stores, including desktop, web, CLI, workers, scheduler, recovery, and tests.
2. Freeze the schema contract and define a versioned migration with deterministic legacy-to-ledger mapping.
3. Build a read-only migration inventory reporting row counts, integrity, duplicate identifiers, unresolved operations, and destination collisions.
4. Require owner-controlled backup and a verified restore drill before migration.
5. Quiesce every writer under the canonical maintenance fence. The fence is sufficient only after every writer is proven to participate.
6. Import into a new database without modifying originals; validate counts, relationships, action digests, event ordering, and unresolved states.
7. Run dual-read comparison in non-dispatching mode. Do not dual-write independently to both schemas.
8. Require explicit owner cutover approval; retain originals read-only for a defined rollback period.
9. Keep consequential dispatch disabled until crash-injection, concurrency, idempotency, restore, and live-session tests pass in CI and on the target deployment environment.

## Required test matrix

- Failure after each insert/update/event append inside each transaction: all changes roll back together.
- Concurrent approval attempts: exactly one conditional transition succeeds.
- Concurrent dispatch claims: exactly one claim succeeds.
- Process termination after claim, after started marker, during target call, and after target success but before result commit.
- Duplicate delivery with target idempotency enabled and disabled.
- Session revocation between confirmation, claim, and immediate pre-dispatch validation.
- Policy, handler, or target-scope revision changes after approval.
- Disk full, busy timeout, I/O error, corrupt database, invalid event chain, and restore from backup.
- Maintenance fence contention and process crash while holding the fence.
- No test may treat a missing completion event as proof that the external side effect did not occur.

## Enablement gate

This proposal does not change the current runtime. The existing separate-store implementation remains in place and consequential specialist execution remains disabled until the unified ledger is implemented, validated, and explicitly enabled by trusted composition.

## Production composition prerequisite

The source-trace snapshot in [APPROVAL_STORE_COMPOSITION_INVENTORY.md](APPROVAL_STORE_COMPOSITION_INVENTORY.md) found the native desktop auth/workflow stores composed under one shared maintenance fence, but did not identify a production composition for the separate approval stores/coordinator/gateway in the inspected desktop startup path. The end-to-end guarded approval graph is currently demonstrated by tests. Treat approval execution as uncomposed in that path until a trusted host composition root is implemented and verified. The inventory is scoped and does not claim a complete repository-wide call graph; it records the exact search limitation and remaining writer-holder audit. Do not begin ledger migration or enable consequential dispatch based only on the test fixture.
