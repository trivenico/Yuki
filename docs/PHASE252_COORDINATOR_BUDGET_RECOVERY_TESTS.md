# Phase 252 — Coordinator-level hierarchical budget verification

## Added coverage

The coordinator test suite now exercises the budget lifecycle through the real plan/delegation APIs:

- A root plan allocates its plan envelope and subtask envelopes.
- A delegated plan receives its envelope from the delegating subtask budget, not a fresh root snapshot.
- Coordinator reconstruction against the same databases reuses the same delegated budget ID.
- Advancing a budgeted plan persists the opaque budget ID in the workflow step definition and repeated advancement reuses the same task.
- Exhausting the delegating subtask envelope prevents delegated plan allocation and blocks both child and parent dispatch.

## Correctness adjustment

The child plan's requested plan policy is clamped to the source subtask snapshot limits before allocation. This prevents a policy callback that asks for a larger child-plan envelope from accidentally exceeding the delegator's available allowance.

## Important boundary

Coordinator and budget store writes remain separate SQLite transactions. This is deliberately handled through deterministic identities, idempotent allocation, and reconciliation; it is not described as a distributed atomic commit.

## Verification status

Tests are committed but have not been executed here. The GitHub workflow-run lookup returned no associated runs for the test commit. No passing-test or production-readiness claim is made.

## Remaining work

- Run the focused coordinator and budget lifecycle suites in CI.
- Add crash injection between allocation and coordinator-state persistence.
- Verify actual worker-side budget metadata and provider-call prevention with coordinator-created tasks.
- Add duration leases/accounting and tool-call instrumentation before claiming full resource enforcement.
