# Phase 51 — Fail-closed production approval composition

## Audit finding

`ApprovalCoordinator` previously allowed construction without a durable pending store and without an audit log. In that configuration, pending approvals lived only in process memory and approval decisions had no durable audit trail. Accidentally using that composition in production could lose state across restart.

## Changes

- When `YUKI_ENV=production`, `ApprovalCoordinator` now requires both an injected pending store and an audit log.
- Development and tests retain the in-memory defaults for lightweight use.
- Added `tests/test_phase51_production_approval_composition.py` covering missing dependencies, valid durable injection, and development behavior.

## Limitations

This guard prevents accidental in-memory/no-audit production composition. It does not make pending approval records, audit events, and dispatcher tokens one atomic transaction. A crash can still occur between those stores' transitions; Phase 52 should add explicit lifecycle states and recovery semantics or move the lifecycle into a single transactional store. Production skill execution remains disabled until the OS sandbox gate is satisfied.
