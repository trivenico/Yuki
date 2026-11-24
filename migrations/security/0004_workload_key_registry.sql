-- WI-6U3: shared PostgreSQL workload public-key lifecycle and immutable audit.
CREATE TABLE IF NOT EXISTS workload_keys (
    issuer TEXT NOT NULL CHECK (length(issuer) BETWEEN 1 AND 512 AND issuer = btrim(issuer) AND issuer !~ '[^\x21-\x7E]'),
    key_id TEXT NOT NULL CHECK (length(key_id) BETWEEN 1 AND 256 AND key_id = btrim(key_id) AND key_id !~ '[^\x21-\x7E]'),
    algorithm TEXT NOT NULL CHECK (algorithm = 'EdDSA'),
    public_key BYTEA NOT NULL CHECK (octet_length(public_key) = 32),
    state TEXT NOT NULL CHECK (state IN ('staged','active','retired','revoked')),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (issuer, key_id)
);
CREATE INDEX IF NOT EXISTS idx_workload_keys_active_lookup
    ON workload_keys (issuer, key_id) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS workload_key_audit (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    administrator TEXT NOT NULL,
    issuer TEXT NOT NULL,
    key_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('register','active','retired','revoked')),
    previous_state TEXT CHECK (previous_state IS NULL OR previous_state IN ('staged','active','retired','revoked')),
    new_state TEXT NOT NULL CHECK (new_state IN ('staged','active','retired','revoked')),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_workload_key_audit_issuer_event
    ON workload_key_audit (issuer, event_id);

CREATE OR REPLACE FUNCTION prevent_workload_key_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'key_audit_immutable';
END;
$$;
DROP TRIGGER IF EXISTS workload_key_audit_no_update ON workload_key_audit;
CREATE TRIGGER workload_key_audit_no_update
    BEFORE UPDATE ON workload_key_audit
    FOR EACH ROW EXECUTE FUNCTION prevent_workload_key_audit_mutation();
DROP TRIGGER IF EXISTS workload_key_audit_no_delete ON workload_key_audit;
CREATE TRIGGER workload_key_audit_no_delete
    BEFORE DELETE ON workload_key_audit
    FOR EACH ROW EXECUTE FUNCTION prevent_workload_key_audit_mutation();
