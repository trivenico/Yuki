# Phase 223 — explicit task/span correlation

Coordinator dispatch now binds each deterministic specialist span to the actual TaskRuntime task ID. Worker tracing resolves that span only by exact task ID and agent/task-type identity, then records worker lifecycle events on the same span. Standalone worker tasks still create their own trace. Conflicting task bindings are rejected.

The plan and trace stores are separate SQLite databases. A crash between enqueue and binding can leave a task temporarily uncorrelated; stable dispatch idempotency prevents duplicate task creation during recovery. Provider execution IDs are not yet automatically linked. This tracing layer never replays provider operations.
