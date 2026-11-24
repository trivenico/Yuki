-- WI-6U4: globally ordered, append-only authorization decision chain.
CREATE TABLE IF NOT EXISTS authorization_audit_state (
    singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
    last_event_id BIGINT NOT NULL CHECK (last_event_id >= 0),
    head_hash TEXT NOT NULL CHECK (head_hash ~ '^[0-9a-f]{64}$')
);
INSERT INTO authorization_audit_state(singleton, last_event_id, head_hash)
VALUES (1, 0, repeat('0', 64))
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS authorization_audit_events (
    event_id BIGINT PRIMARY KEY CHECK (event_id > 0),
    occurred_at TEXT NOT NULL,
    principal_hash TEXT NOT NULL,
    tenant_hash TEXT,
    agent_hash TEXT,
    action TEXT NOT NULL,
    resource_hash TEXT NOT NULL,
    task_hash TEXT,
    delegation_hash TEXT,
    approval_hash TEXT,
    risk TEXT NOT NULL,
    allowed SMALLINT NOT NULL CHECK (allowed IN (0,1)),
    decision_hash TEXT NOT NULL,
    policy_revision_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS authorization_audit_time_idx
    ON authorization_audit_events(event_id, occurred_at);

CREATE OR REPLACE FUNCTION reject_authorization_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'authorization_audit_immutable';
END;
$$;
DROP TRIGGER IF EXISTS authorization_audit_events_no_update ON authorization_audit_events;
CREATE TRIGGER authorization_audit_events_no_update
    BEFORE UPDATE OR DELETE ON authorization_audit_events
    FOR EACH ROW EXECUTE FUNCTION reject_authorization_audit_mutation();
