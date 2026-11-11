# Phase 211 — durable task claim fencing

## Implemented contract

Each successful `DurableTaskEngine.claim` creates a cryptographically random, per-claim token and stores it with the running task. The token is returned only to the claiming worker; ordinary task reads and decoded task projections omit it. Heartbeat, completion, failure, and rejection require the exact token and verify it with constant-time comparison inside the same SQLite write transaction as the state transition.

Recovery clears the previous token before making a task eligible for another claim. A subsequent claim receives a different token even when the same worker ID is reused. Stale tokens cannot renew, complete, fail, or reject the reassigned task.

The durable skill worker carries the token through heartbeat and terminal writes. It also supplies a lease validator to `ApprovedSkillRuntime`; the shared guarded-skill boundary checks that validator immediately before invoking the skill's execute/run handler. A failed or unavailable lease check fails closed.

## Migration and compatibility

The schema change is additive: existing task databases receive a nullable `lease_token` column. Existing in-flight legacy rows have no token and therefore cannot be mutated through the new worker lifecycle methods. They must be reconciled through supervised recovery rather than silently trusted. The claim token is never included in ordinary `get_task` projections or event detail.

## Security boundaries and remaining gaps

This fences the local SQLite task state and the skill dispatch boundary. It does not terminate a stale process, revoke a remote provider credential, or undo a side effect already in progress. A handler that performs multiple external writes must still enforce revocable credentials/fencing at each external commit boundary and use idempotency or an outbox where supported. SQLite remains a single-host store, not a distributed consensus system.

## Validation

Regression coverage includes token redaction from task reads, mandatory claim tokens, same-worker reassignment, rejection of stale heartbeat/completion/failure/rejection attempts, and the worker's dispatch-time lease guard. Cross-process and real external-adapter tests, exact-head CI, and operational recovery procedures remain release gates.
