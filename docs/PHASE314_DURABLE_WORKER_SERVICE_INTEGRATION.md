# Phase 314 — Durable worker service integration

## Scope

Connects the supervised reviewer worker service to the Phase 313
`ReviewerWorkerRegistry`. Registry integration is opt-in so existing local
worker deployments remain compatible.

## Runtime behavior

- The service registers its worker instance before starting its executor pool.
- Each concurrent `run_once` call must atomically claim a shared reviewer slot
  before polling/processing dispatches.
- A host-side renewal thread extends both the worker heartbeat lease and its
  active slot while the call is running.
- The slot is released in a `finally` block; an already expired/revoked slot
  is treated as unavailable rather than as a reason to replay a task.
- The service marks its instance stopped during shutdown, which also removes
  that instance's remaining slot reservations.
- Registration failure is fail-closed: no worker execution starts.

## Compatibility and authorization

When no registry is configured, the previous process-local behavior remains.
When configured, `registry_actor_id` is mandatory and every registry operation
uses the registry's trusted authorization callback. The registry lease token
stays in the host service and is not passed to the reviewer worker or executor.

## Recovery and fencing boundary

A slot lease is a coordination reservation, not a task execution receipt.
If a worker stalls beyond its lease and continues executing, this service
cannot forcibly terminate its Python thread. The slot may become available to
another process. Therefore the reviewer task ledger's lease generation and
fencing checks remain mandatory to prevent stale execution from committing
results. The service does not automatically replay in-progress tasks.

## Deployment

All instances for one reviewer identity must share the same SQLite registry
database and use the same configured global concurrency cap. SQLite WAL and
locking require a filesystem with supported SQLite semantics; do not treat a
shared network filesystem as a distributed database. Use a transactional
shared database or coordination service for multi-host deployments.

## Verification

Run:

```bash
python -m unittest tests.test_reviewer_worker_service tests.test_reviewer_worker_registry -v
```

The service integration test verifies registration, slot acquisition/release,
and clean deregistration. Tests have been authored but not executed by this
change. CI status must be checked before asserting runtime correctness.
