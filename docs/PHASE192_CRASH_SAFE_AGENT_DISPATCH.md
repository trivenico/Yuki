# Phase 192 — crash-safe specialist dispatch idempotency

Specialist dispatch uses a deterministic task idempotency key derived from the
owner-scoped plan key and subtask ID. This phase adds a regression test for the
critical crash window where the durable task enqueue commits but the coordinator
fails before persisting the returned task ID into the plan.

A fresh coordinator can safely call advance again: the task store's transactional
idempotency check returns the existing run for the same request digest rather
than creating a second task. The plan then binds to that existing task.

The test simulates the failure at the enqueue/bind boundary and verifies that
recovery creates exactly one workflow run. This does not authorize replay of
external side effects, and it does not retry failed/interrupted tasks. Those
require separate operator-approved retry policy and operation-level idempotency.
