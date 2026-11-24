-- WI-6U13C: PostgreSQL-native workload delegation and append-only audit.
CREATE TABLE IF NOT EXISTS workload_delegations (
    delegation_id TEXT PRIMARY KEY,
    grantor_id TEXT NOT NULL,
    grantee_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    lease_generation TEXT NOT NULL,
    scopes_json JSONB NOT NULL,
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    parent_id TEXT REFERENCES workload_delegations(delegation_id),
    revoked_at BIGINT,
    CHECK (issued_at >= 0 AND expires_at > issued_at),
    CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
);
CREATE INDEX IF NOT EXISTS workload_delegations_parent_idx ON workload_delegations(parent_id);
CREATE INDEX IF NOT EXISTS workload_delegations_task_idx ON workload_delegations(task_id, lease_generation);
CREATE TABLE IF NOT EXISTS workload_delegation_events (
    event_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('issued','verified','revoked')),
    delegation_id TEXT NOT NULL REFERENCES workload_delegations(delegation_id),
    actor_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    scopes_json JSONB NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS workload_delegation_events_grant_idx
    ON workload_delegation_events(delegation_id, event_id);
