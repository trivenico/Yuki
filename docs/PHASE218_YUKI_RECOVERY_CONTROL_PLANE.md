# Phase 218 — Yuki Recovery Control Plane

## Delivered

- Read-only aggregate snapshot across provider execution and workflow operation journals.
- Bounded query limits for both journals.
- Stale activity detection with configurable bounded threshold.
- Separate counts for running/uncertain provider executions and in-flight/reconciliation workflow operations.
- Explicit health status and machine-readable signals.
- No mutation, provider calls, cancellation, resolution, or retry.

## Important correlation gap

Provider execution records currently do not have a universal workflow operation foreign key. This phase intentionally reports correlation as `not_implemented`. It does not guess matches from provider names, timestamps, or request digests. A later schema migration must add a durable explicit correlation record and safely backfill only where evidence exists.

## Clock and storage notes

Staleness is calculated from the supplied/current UTC clock. Clock skew can affect stale classification. The underlying SQLite journals remain separate and are not read under one shared transaction, so the snapshot is an operational view, not a globally consistent point-in-time snapshot.
