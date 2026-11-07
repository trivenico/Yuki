# Phase 290 — Durable reviewer task ledger

## Implemented

Added `automation/reviewer_ledger.py`, a SQLite-backed lifecycle ledger for reviewer tasks. It persists:

- The canonical task contract and its SHA-256 digest.
- Artifact digest and assigned reviewer.
- Lifecycle state: `assigned`, `in_progress`, or `submitted`.
- Versioned state transitions.
- A task-scoped append-only event history.
- The canonical structured result and its digest.

The ledger requires a trusted authorization callback for every operation. Its contract is `(actor_id, action, artifact_sha256) -> bool`, and only literal `True` authorizes an operation. Assignment, claim, submit, and read use distinct permissions: `review:assign`, `review:execute`, `review:submit`, and `review:read`.

Only the assigned reviewer may claim or submit that task. Claim and submission use compare-and-swap versions. Exact repeated task creation and exact repeated result submission are idempotent; conflicting reuse is rejected. A result is validated against the persisted task contract before it can be stored.

## Trust boundaries

The ledger does not execute a reviewer, verify that evidence references resolve, authenticate identities itself, or create a `VerificationReport`. The authorization callback must resolve actor IDs to trusted principals. A submitted result is an auditable reviewer assertion, not independently proven truth. The verification evidence gate remains separate and fail-closed.

The ledger does not expose a task listing API in this phase; it only supports reading a known task under `review:read`. This avoids adding a broad metadata-discovery surface before a supervisor listing policy is designed.

## Tests and verification

Added `tests/test_reviewer_ledger.py` for idempotent creation, reviewer-only claims, stale version rejection, result persistence, exact retry behavior, conflicting submission rejection, authorization failure, and submission-before-claim rejection.

Tests are authored but have not been executed in this environment. No CI status checks are claimed.
