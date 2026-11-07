# Phase 254 — End-to-end worker budget boundary tests

## Corrected test fixtures

- Specialist worker budget tests now create a root snapshot and allocate a child snapshot. The worker intentionally rejects root snapshots as specialist execution budgets.
- The local runtime fake provider now records prompts so provider invocation assertions are meaningful.

## Added worker security coverage

- A valid child budget permits the provider handler and records model-turn and input-character reservations.
- A task identity mismatch fails before handler invocation and leaves usage unchanged.
- A missing execution metadata block fails closed when the worker has a budget store configured.
- Exhausting a shared child budget across separate queued tasks allows only the first provider call; the later task fails before the handler runs.

## Important semantics

Reservations are charged before handler execution. If a worker crashes after reservation, the attempt is conservatively charged. A retried task has a new claim attempt key; cumulative usage remains on the same immutable child snapshot and is not reset.

## Verification status

Changes are committed, but tests have not been executed in this environment. No CI pass is claimed.

## Next

Run focused unit suites and add a real coordinator-created task through the specialist worker in one integrated test. Then validate the top-level main.py startup path and define an operator-visible local-use readiness checklist.
