# Phase 309 — Lease-aware initial reviewer dispatch

## Scope

Closes the gap between reviewer-team planning and execution by making initial
reviewer task creation and lease issuance one durable SQLite transaction.
Provisioned reviewer credentials now carry the complete task contract and
lease generation required by the host execution adapter.

## Initial dispatch path

1. The coordinator passes authorization and deterministic reviewer-team planning.
2. The orchestrator validates all task contracts.
3. It issues artifact-scoped reviewer sessions; partial session issuance is
   revoked if a later issuance fails.
4. The ledger opens `BEGIN IMMEDIATE`.
5. For each new task, it writes the task row and assignment event, then calls
   `ReviewerLeaseAuthority.acquire_in_transaction` on the same connection.
6. The lease row and lease audit event are written before commit.
7. The orchestrator returns `ProvisionedReviewerTask` values containing the
   session token, lease token, generation, and validated `ReviewTask`.

If lease issuance or persistence fails, SQLite rolls back task and lease
records together. The orchestrator revokes every session it minted for the
failed batch. Ledger and lease authorities must resolve to the same database
file; paths are compared after normalization.

## Execution handoff

`ReviewerExecutionAdapter.execute` can now take the task contract directly
from `ProvisionedReviewerTask` when the caller omits a separate task argument.
It uses the provisioned lease token and generation for both claim and submit.
Recovered tasks also carry a newly constructed contract bound to the replacement
reviewer identity, avoiding the stale reviewer identity embedded in the
original immutable task payload.

## Idempotency boundary

The low-level task ledger still supports idempotent task creation. The
orchestrator intentionally fails closed when it encounters an already-existing
task without a newly issued lease token; it does not fabricate or expose a
replacement credential. A dedicated resume operation remains future work.

## Tests

Added regression coverage for:
- Initial batch provisioning with active leases.
- Lease token, generation, reviewer, and artifact binding.
- Atomic rollback when lease authorization fails.
- Recovered task contract identity.
- Execution using the provisioned contract without a separate task argument.

Tests are authored but have not been executed. No claim of CI success is made.
