# Phase 212 — Owner-bound durable goal identity

## Status

Implemented as a foundational persistence boundary. This phase is not a complete authenticated task API or an integrated approval/worker authorization system.

## Changes

- Added nullable `goals.owner_id` with an additive SQLite migration.
- Added `create_owned_goal(owner_id)`; owner identifiers are bounded, trimmed strings.
- Added `submit_for_owner(owner_id, ...)`; the goal-owner comparison is performed inside the same `BEGIN IMMEDIATE` transaction that checks task idempotency and inserts the task.
- Added owner-scoped goal/task projections, task/event listing, goal-state mutation and task cancellation.
- Cross-owner access returns the same not-found result as missing records to avoid disclosing resource existence.
- Goal ownership is immutable through the engine API.
- Legacy goals migrate as explicitly unowned (`owner_id IS NULL`) and are not exposed by owner-scoped methods. They are not silently assigned to the first caller.
- Audit event for goal creation records only whether an owner binding exists, never the owner identifier.

## Trust boundary

The `owner_id` argument is trusted input only when supplied by an authenticated application service after deriving the principal from its verified session. It must never be copied from a request body, task payload, model output, or caller-selected header.

The legacy `create_goal()`, unscoped `get_*`, `list_*` and mutation methods remain available for existing local engine consumers. They are not an authenticated API boundary. The durable skill worker does not yet derive policy context from owner-bound records in this phase.

## Migration and compatibility

The migration adds one nullable column; existing goal and task records remain intact. Existing records are intentionally unowned. An operator must not backfill ownership from payloads or guessed local identity. A future authenticated migration/import path must establish provenance explicitly.

## Security and correctness limits

- SQLite is a single-host persistence engine, not distributed consensus.
- Owner-scoped wrappers currently guard local service access; the authenticated task API and its routes are not implemented here.
- Worker claim, recovery and approval credential resolution are not yet joined to owner identity.
- Legacy unscoped engine methods can bypass owner checks if exposed directly to untrusted callers; callers must keep the engine behind the trusted service boundary.
- No remote side-effect fencing, KMS credential encryption, or provider commit idempotency is added by this phase.

## Acceptance coverage

Regression tests cover cross-owner goal/task/event access, task submission and mutation denial, legacy unowned records, identity validation, and persistence across engine restart. Repository CI is the test authority; no local test execution is claimed.
