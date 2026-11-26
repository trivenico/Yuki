-- WI-6U13K: keep dependency readiness separate from dispatch-envelope visibility.
-- dispatch_ready remains the outbox/visibility gate; dependency_ready is controlled
-- only by the workflow goal dependency graph.
ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS dependency_ready BOOLEAN NOT NULL DEFAULT TRUE;
