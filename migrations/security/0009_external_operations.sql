-- WI-6U13I: durable intent journal for explicitly authorized external operations.
-- This schema does not dispatch provider calls or promise exactly-once execution.
CREATE TABLE IF NOT EXISTS external_operations (
    operation_id TEXT PRIMARY KEY CHECK (operation_id ~ '^[0-9a-f]{32}$'),
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    step_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    action_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN (
        'pending','inflight','succeeded','failed_terminal','unknown','reconciliation_required'
    )),
    result_digest TEXT CHECK (result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'),
    provider_reference TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    CHECK (
        (state = 'inflight' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (state <> 'inflight' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS external_operations_pending_idx
    ON external_operations(created_at, operation_id) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS external_operations_task_idx
    ON external_operations(task_id, created_at, operation_id);
CREATE INDEX IF NOT EXISTS external_operations_expired_idx
    ON external_operations(lease_expires_at) WHERE state = 'inflight';

CREATE TABLE IF NOT EXISTS external_operation_events (
    event_id BIGSERIAL PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES external_operations(operation_id) ON DELETE RESTRICT,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    kind TEXT NOT NULL CHECK (kind IN (
        'operation.created','operation.claimed','operation.succeeded',
        'operation.failed_terminal','operation.unknown','operation.reconciled'
    )),
    actor_id TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(operation_id, sequence)
);
CREATE INDEX IF NOT EXISTS external_operation_events_sequence_idx
    ON external_operation_events(operation_id, sequence);
