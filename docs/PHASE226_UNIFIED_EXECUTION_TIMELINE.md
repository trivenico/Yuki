# Phase 226 — unified execution timeline

- Added a read-only execution_timeline query for one root execution.
- Returns bounded execution nodes, explicit parent/root links, task/provider execution IDs, status and timestamps.
- Returns allowlisted lifecycle and authorization events in stable occurred_at/event_id order.
- Aggregates token, duration and caller-reported cost counters across the root tree.
- Enforces strict node/event limits and fails rather than returning a misleading partial timeline.
- Reads all timeline components in one SQLite connection and exposes no prompts, request/response bodies, credentials, exception text or arbitrary metadata.

This is a store-level inspection API, not yet an HTTP endpoint or user interface. Cost and token totals remain caller-reported; missing instrumentation is not evidence of zero usage. Separate task/provider/trace databases can still leave correlation gaps. No execution is retried or mutated by this API.
