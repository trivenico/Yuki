# Phase 39 — Durable one-time approval tokens

## Audit finding

Phase 38 introduced payload-bound one-time approval tokens, but the dispatcher used an in-memory store while the approval coordinator had a separate SQLite pending-request store. This created two lifecycle/state systems and meant dispatcher tokens did not survive process restarts.

## Changes in this phase

- Added `core/sqlite_action_approvals.py`, a SQLite-backed implementation of the action approval store API.
- Binds each token to the skill/action ID, risk level, canonical JSON payload SHA-256, creation time, and expiry.
- Uses a write transaction for token consumption and a conditional `DELETE ... RETURNING` so a token is consumed once; mismatches burn the token and fail closed.
- Applies bounded payload size, pending-request capacity, expiry cleanup, strict JSON validation, and a busy timeout.
- Added tests covering restart persistence, replay, action/payload/risk mismatch, expiry, non-JSON inputs, and capacity.

## Integration note

The new durable store implements the same API accepted by `SkillDispatcher(approval_store=...)`. Configure the dispatcher with `SQLiteActionApprovalStore(<private data path>)` to enable durable tokens. The default remains the in-memory store for compatibility, so deployments must explicitly wire the SQLite store before relying on restart durability. The coordinator's pending request and dispatcher token are still separate records; this phase does not make approval confirmation plus dispatch a single cross-store transaction.

## Remaining blockers

- Wire the SQLite store into the canonical application composition root and prevent production configuration from silently using the in-memory default.
- Make coordinator confirmation and dispatcher token minting a single state transition, or have the coordinator consume the same durable record directly.
- Migrate all legacy in-process skill/plugin calls through one invocation facade.
- Add OS-level sandboxing; a subprocess runner is not itself a sandbox.
- Run full latest-head CI and independent security review.

## Verification

This phase adds focused regression tests. It must not be marked verified until CI passes on the latest head commit. PR remains draft and must not be merged before the release gates pass.
