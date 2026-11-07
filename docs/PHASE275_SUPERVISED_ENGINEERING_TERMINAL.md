# Phase 275 — Supervised engineering terminal controls

## Delivered

Updated `main.py` with three operator-facing commands backed by the existing
local specialist runtime:

- `/engineering status` reports specialist count, concurrency ceiling, durable
  store locations, whether a trusted verification executor is configured, and
  whether Git workspace orchestration is connected.
- `/engineering agents` lists sanitized specialist identities, capabilities
  and enablement state through the coordinator's authorized registry API.
- `/engineering plan ROLE | DESCRIPTION` asks for explicit operator approval,
  then persists one approval-gated subtask routed to the selected registered
  specialist. It does not dispatch work or invoke a provider.

The plan command uses a fresh opaque idempotency key. The specialist role must
already be registered, and the coordinator performs its own capability routing
and validation.

## Safety boundaries

This is a supervised planning interface, not a complete engineering console.
It intentionally does not expose plan advancement, task approval, worker cycles,
verification execution, artifact browsing or self-upgrade from this command
surface yet. No background worker or automatic task execution was enabled.

The ordinary `main.py` startup still creates the local runtime without a
verification command allowlist, so verification execution remains disabled.
The Git-backed workspace manager is not yet instantiated by terminal startup.

## Verification status

The main module was updated through the repository integration. Tests and
interactive startup were not run in this environment. The latest commit has no
reported combined status checks; no test or production-readiness claim is made.
