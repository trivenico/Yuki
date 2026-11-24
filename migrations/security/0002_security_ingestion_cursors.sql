-- WI-6U2: shared PostgreSQL ingestion cursor and fenced source lease.
CREATE TABLE IF NOT EXISTS security_ingestion_cursors (
    source TEXT PRIMARY KEY CHECK (source IN ('authorization_audit', 'workload_key_audit')),
    event_id BIGINT NOT NULL DEFAULT 0 CHECK (event_id >= 0),
    lease_owner TEXT,
    lease_token TEXT,
    lease_epoch BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_until TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_code TEXT,
    CHECK (
        (lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)
        OR
        (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_security_ingestion_cursors_lease_until
    ON security_ingestion_cursors (lease_until);
