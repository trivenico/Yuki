# Phase 106 — Reconciliation-to-ledger integration

## Scope
- Connect the pure conflict classifier to the local SQLite conflict ledger.
- Persist only concurrent or ancestry-unknown conflicts.
- Treat identical and explicitly sequential versions as no-ops.
- Preserve both candidates and never choose a winner automatically.
- Add tests for persistence, idempotency, no-op cases, and malformed input.

## Boundaries
This remains a local integration primitive. It does not enable network synchronization, authenticated device pairing, runtime startup wiring, automatic memory migration, encrypted backups, or cloud deployment. The adapter expects Phase 103's `core/memory_conflicts.py` to be present on the target branch when the PR is integrated.

## Verification required
Run the repository's full test suite and CI against this branch. Keep this PR draft until the Phase 103 and Phase 105 dependencies are reviewed and their merge order is agreed.
