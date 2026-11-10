# Phase 98 — Audited workflow cancellation

## Implemented

- Cancellation is performed by one SQLite transaction using an immediate write lock.
- The requested version is checked before changing state; stale requests fail without changing state or writing an event.
- State transition to `cancelled`, lease clearing, and a `task.cancelled` audit event are committed together.
- Actor and machine-readable reason codes are bounded and validated.
- The task runtime delegates to this atomic store operation rather than writing state and event in separate transactions.
- Regression tests cover the atomic state/event contract, stale versions, terminal runs, and input validation.

## Important execution limitation

This API persists cancellation state; it does not forcibly terminate a running Python thread, process, or external provider request. An executing worker must cooperatively check persisted cancellation state before starting each side effect, and provider calls need bounded timeouts. Do not describe this as hard process cancellation. No interrupted work is automatically replayed.

## Validation

The changes are committed to the phase branch. Fresh CI for the latest head must pass before the PR is considered mergeable. Keep the PR draft until the tests and security review complete.
