# Phase 109 — durable task event stream

## Delivered

- Added `automation/task_event_stream.py` as a transport-neutral event replay boundary.
- Reads task lifecycle events from the existing SQLite workflow event journal through `TaskRuntime`.
- Enforces `task:read` authorization for every batch.
- Adds bounded cursor pagination (sequence cursor, maximum page size 499 (with one-record lookahead), one-record lookahead).
- Emits Server-Sent Events frames with monotonically increasing per-task sequence IDs, event names and JSON data.
- Supports reconnect/replay by passing the last successfully consumed sequence as `after_sequence`.
- Extended `TaskRuntime.events()` with a bounded limit parameter while preserving its authorization check.
- Added tests for event replay, SSE framing, cursor validation, authorization and type safety.
- Registered the capability as experimental.

## Design boundaries

- The SQLite task event journal remains the source of truth; the in-process `EventBus` is not treated as durable.
- This phase supplies SSE framing and polling/replay primitives only. It does not open network listeners or manage long-lived HTTP connections.
- No WebSocket server, desktop push channel, global cross-task event cursor, retention policy, compaction or event outbox is implemented.
- Clients must persist the last processed sequence and reconnect with that cursor. Duplicate delivery is possible if a client processes an event but crashes before saving its cursor; clients should deduplicate by task ID and sequence.
- Authorization is re-evaluated for each batch. The HTTP/desktop adapter must authenticate the principal and enforce workspace/task ownership before exposing a stream.
- Task events remain scoped to a task; conversation-level events and agent-plan aggregate events require a later adapter.

## Validation

```bash
python -m unittest tests.test_task_event_stream -v
python -m unittest tests.test_task_runtime -v
python -m unittest tests.test_workflow_store -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

Require exact-head Linux and Windows CI before merge. This phase is stacked on Phase 108.
