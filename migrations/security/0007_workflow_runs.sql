-- WI-6U13B: shared, transactional workflow state and task-event journal.
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    workflow_name TEXT NOT NULL,
    definition_json JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','waiting_approval','succeeded','failed','cancelled','interrupted')),
    result_json JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    approval_digest TEXT,
    approved_session_hash TEXT,
    approval_id TEXT,
    CHECK ((state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS workflow_runs_state_created_idx ON workflow_runs(state, created_at, run_id);
CREATE INDEX IF NOT EXISTS workflow_runs_lease_expiry_idx ON workflow_runs(state, lease_expires_at);
CREATE INDEX IF NOT EXISTS workflow_runs_approved_session_idx ON workflow_runs(approved_session_hash, created_at, run_id)
    WHERE state='queued' AND approved_by IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_events (
    event_id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(task_id, sequence)
);
CREATE INDEX IF NOT EXISTS task_events_task_sequence_idx ON task_events(task_id, sequence);
