-- WI-6U1: shared, PostgreSQL-authoritative security work leases.
-- Apply through the deployment migration process; runtime code never auto-creates schema.
CREATE TABLE IF NOT EXISTS security_work_leases (
    queue_name TEXT NOT NULL CHECK (length(queue_name) BETWEEN 1 AND 96),
    resource_key TEXT NOT NULL CHECK (length(resource_key) BETWEEN 1 AND 200),
    lease_owner TEXT NOT NULL CHECK (length(lease_owner) BETWEEN 1 AND 96),
    lease_token TEXT NOT NULL CHECK (length(lease_token) = 32),
    lease_epoch BIGINT NOT NULL CHECK (lease_epoch > 0),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (queue_name, resource_key)
);
CREATE INDEX IF NOT EXISTS idx_security_work_leases_expiry
    ON security_work_leases (lease_expires_at);
