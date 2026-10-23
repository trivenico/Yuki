# Phase 78 — atomic workflow claims and lease renewal

## Implemented

- Atomically claim the oldest queued workflow run using a SQLite immediate transaction.
- Assign each claim to a bounded worker identifier with a bounded lease and increment the attempt/version counters.
- Prevent concurrent workers from claiming the same queued run.
- Allow lease renewal only for the current worker, matching version and an unexpired lease.
- Preserve the no-automatic-replay rule: expired running work is reconciled as interrupted, not requeued.
- Add concurrent-claim and lease-owner/version/expiry regression tests.

## Scope boundary

This phase provides worker coordination primitives only. It does not launch a permanent background service, schedule recurring jobs, expose webhooks, execute workflow handlers, or provide n8n-compatible integrations. Those should be layered on after a worker supervisor, actor-bound approval adapter, bounded step execution, and real multi-process/SQLite failure-injection tests exist.

## Operational rule

A lease is a coordination mechanism, not proof that a process stopped. Never treat an expired lease as permission to replay a step with possible external side effects. Reconcile ambiguous runs explicitly.
