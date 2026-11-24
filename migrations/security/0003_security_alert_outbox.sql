-- WI-6U3: durable PostgreSQL security alert delivery outbox.
CREATE TABLE IF NOT EXISTS security_alert_outbox (
    alert_id TEXT PRIMARY KEY CHECK (alert_id ~ '^[0-9a-f]{32}$'),
    incident_id TEXT NOT NULL CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    payload JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'inflight', 'delivered', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_epoch BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_owner TEXT,
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    last_error_code TEXT,
    delivered_at TIMESTAMPTZ,
    CHECK (
        (state = 'inflight' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR
        (state <> 'inflight' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_security_alert_outbox_due
    ON security_alert_outbox (available_at, created_at)
    WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS idx_security_alert_outbox_expired
    ON security_alert_outbox (lease_until, created_at)
    WHERE state = 'inflight';
CREATE INDEX IF NOT EXISTS idx_security_alert_outbox_state_created
    ON security_alert_outbox (state, created_at DESC);
