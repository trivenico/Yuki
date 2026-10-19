# Runtime composition root — Phase 23

## Design

`core/runtime_composition.py` owns construction of the runtime queue and worker service. It creates a durable SQLite queue beneath the configured workspace at `runtime/jobs.sqlite3`, registers one harmless built-in handler (`system.health`), and uses a strict authorizer that rejects all other task types or payloads.

The composition root deliberately does not dynamically discover or import handlers. To add a task, register a reviewed handler in code and extend the authorizer with a task-specific policy. Sensitive handlers must delegate to the approval/permission coordinator; do not use a blanket allow-all authorizer.

## Current boundaries

- This module is available for application integration and test usage; it does not start background workers automatically.
- The interactive CLI is not yet wired to submit/monitor runtime jobs; that is an explicit follow-up to avoid silently starting threads or persistent background work on every launch.
- Authentication is not relevant to the current in-process constructor, but any API or remote submission adapter must authenticate the caller and derive identity server-side.
- SQLite is intended for a single-host local workspace, not shared network storage.
- Lease renewal/fencing and cancellation cooperation for long-running handlers remain incomplete.

## Release gate

Run the full suite on the exact PR head. Before enabling long-lived workers, add explicit lifecycle integration in the application entry point, startup/shutdown handling, queue/lease telemetry, and tests for cancellation and stale-worker fencing.
