# Phase 236 — Clean asset-to-task binding

## Delivered

- Single-task creation validates every non-null `asset_ref` against the configured asset store before creating the task.
- Batch task creation validates every referenced asset before calling the atomic batch insert; one invalid, foreign, missing, deleted or quarantined asset rejects the entire batch.
- Asset references must resolve to a clean asset owned by the authenticated user and scoped to the same project.
- When asset storage is not configured, tasks with no asset reference remain supported; tasks that reference assets fail closed.
- SQLite metadata lookups run through `asyncio.to_thread` so synchronous storage operations do not block the ASGI event loop.
- Adds API regression coverage for clean references, quarantined references, cross-owner references and all-or-nothing batch rejection.

## Security and consistency notes

- The task API never accepts filesystem paths or remote URLs as asset references.
- This validation is a pre-insert check across two independent SQLite stores, not a cross-database transaction. A concurrent asset deletion can still race between validation and task insertion; reads must continue to resolve and authorize the asset at use time. A future unified transactional asset/task repository or explicit reference lifecycle is needed to eliminate that race.
- Clean status means the configured scanner explicitly returned boolean `True`; it is not a guarantee that a deployment has a sufficiently capable malware/content scanner.
- This phase does not add inline browser preview, media decoding, waveform/timeline controls, marketplace delivery, or payment handling.
