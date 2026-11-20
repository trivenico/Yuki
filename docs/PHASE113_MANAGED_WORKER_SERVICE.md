# Phase 113 — Managed specialist worker service

## Scope

Add an explicit, thread-safe lifecycle gate around the existing bounded
WorkerSupervisor. This is not an always-on scheduler and does not start
background threads or automatically execute queued work.

## Lifecycle contract

- `start()` opens admission for explicitly requested cycles; it runs no work.
- `pause()` rejects new cycles and lets the current finite cycle finish.
- `resume()` reopens admission from the paused state.
- `run_cycle()` permits one active supervisor cycle at a time; supervisor
  limits still bound tasks per cycle, worker concurrency and lease recovery.
- `stop(timeout)` closes admission and drains the active cycle. It returns
  false on timeout and remains in `stopping`; it never claims to interrupt an
  in-flight provider call. The cycle completion transitions it to `stopped`.
- A repeated stop is safe. `close` aliases `stop`.

## Health surface

`WorkerServiceStatus` exposes lifecycle state, whether a cycle is active,
completed/failed cycle counters, the exception type for a supervisor-level
failure (not exception text), and the last successful cycle report. Individual
specialist task failures remain ordinary task outcomes in the report and durable
task event stream.

## Composition

`build_local_worker_runtime()` now returns both the existing supervisor and a
`ManagedWorkerService` wrapping that exact supervisor. Existing explicit
`supervisor.run_cycle()` callers remain compatible. The service is initially
stopped and is not wired to desktop startup or shutdown; no provider call can
begin until a local caller explicitly starts the service and requests a cycle.

## Safety and limitations

- No automatic background execution, polling loop, daemon or restart replay.
- Stop is graceful drain, not forced cancellation of an active provider request.
- A timeout leaves admission closed in `stopping` until the active cycle exits.
- Expired task leases are still marked interrupted and are never automatically
  replayed by this lifecycle wrapper.
- Status counters are process-local diagnostics, not a durable metrics backend.
- No local test runner was available during this change; CI verification remains
  required. No merge or deployment performed.
