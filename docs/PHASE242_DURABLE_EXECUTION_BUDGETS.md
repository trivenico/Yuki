# Phase 242 — Durable execution-budget contracts

**Status:** Snapshot and atomic reservation store added on the feature branch. Worker call-site enforcement and CI remain outstanding.

## Guarantees

- One immutable budget snapshot per task ID.
- Snapshot digest is checked when loaded.
- Optional parent-budget reference must already exist.
- Resource reservations are append-only and idempotent.
- SQLite `BEGIN IMMEDIATE` serializes concurrent reservations sharing the same database.
- A reservation that would exceed its resource limit is rejected before it is recorded.
- A repeated idempotency key returns the existing accounting result; reusing it for different spend is rejected.

## Resource dimensions

Model turns, tool calls, duration milliseconds, and transcript characters are accounted independently. The schema intentionally does not infer token or provider cost usage when the provider does not report it.

## Safety and integration boundary

This store does not itself stop a provider request or terminate a worker. The worker must reserve before starting the corresponding action and fail closed when reservation is rejected. A parent/child budget allocator must also ensure the sum of child allocations never exceeds the parent's remaining allowance. A parent reference alone does not implement that allocation rule.

Use a shared durable database for cross-process enforcement. Separate local SQLite files cannot provide a global fleet-wide budget.
