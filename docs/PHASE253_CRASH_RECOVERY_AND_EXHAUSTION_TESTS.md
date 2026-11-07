# Phase 253 — Crash-window recovery and allocation exhaustion tests

## Added

- Simulated a crash after the deterministic root budget snapshot is committed but before the coordinator database records its budget ID and allocation state.
- Verified that subsequent coordinator reconciliation retrieves and verifies the existing snapshot, then repairs the coordinator row without minting another budget.
- Corrected the delegated-budget overspend fixture to consume the entire source subtask model-turn allowance rather than one unit. Child delegation must then fail closed.
- Asserted the coordinator's public failure contract (AgentPlanError) rather than accepting arbitrary exceptions.

## Recovery model

The budget store and coordinator state are separate SQLite transactions. The supported recovery strategy is deterministic identity plus immutable snapshot verification and coordinator reconciliation. This is not a cross-database atomic transaction.

## Verification status

The test changes are committed. They have not been executed in this environment. No CI pass is claimed.

## Next validation

Run the focused suites:
- python -m unittest tests.test_agent_budget_lifecycle
- python -m unittest tests.test_agent_coordinator

Then test the coordinator-created workflow through the real specialist worker and provider boundary, including a missing or mismatched budget reference.
