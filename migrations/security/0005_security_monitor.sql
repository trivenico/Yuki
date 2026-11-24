-- WI-6U3: shared normalized security signals, incidents, and incident history.
CREATE TABLE IF NOT EXISTS security_monitor_signals (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE CHECK (length(signal_id) BETWEEN 1 AND 128),
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (source ~ '^[a-zA-Z0-9_.:-]{1,96});
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (source ~ '^[a-zA-Z0-9_.:-]{1,96}
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (source ~ '^[a-zA-Z0-9_.:-]{1,96}
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (source ~ '^[a-zA-Z0-9_.:-]{1,96}
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (source ~ '^[a-zA-Z0-9_.:-]{1,96}
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL CHECK (last_reason_code ~ '^[a-zA-Z0-9_.:-]{1,96}
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
),
    subject_hash TEXT CHECK (subject_hash IS NULL OR subject_hash ~ '^[0-9a-f]{64}$'),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 96)
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_signal_window
    ON security_monitor_signals(category, subject_hash, occurred_at);

CREATE TABLE IF NOT EXISTS security_monitor_incidents (
    incident_id TEXT PRIMARY KEY CHECK (incident_id ~ '^[0-9a-f]{32}$'),
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN (
        'authorization_denied','admin_auth_denied','credential_verification_failed',
        'key_revoked','audit_integrity_failure'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('high','critical')),
    status TEXT NOT NULL CHECK (status IN ('open','acknowledged','resolved')),
    first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    event_count BIGINT NOT NULL CHECK (event_count >= 1),
    last_reason_code TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_monitor_incident_status
    ON security_monitor_incidents(status, last_seen DESC);

CREATE TABLE IF NOT EXISTS security_monitor_incident_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES security_monitor_incidents(incident_id),
    action TEXT NOT NULL CHECK (action IN ('acknowledge','resolve')),
    operator_hash TEXT NOT NULL CHECK (operator_hash ~ '^[0-9a-f]{64}$'),
    occurred_at DOUBLE PRECISION NOT NULL CHECK (occurred_at >= 0)
);

CREATE OR REPLACE FUNCTION reject_security_monitor_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'security_monitor_immutable';
END;
$$;

DROP TRIGGER IF EXISTS security_monitor_signals_no_update ON security_monitor_signals;
CREATE TRIGGER security_monitor_signals_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_signals
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();

DROP TRIGGER IF EXISTS security_monitor_history_no_update ON security_monitor_incident_history;
CREATE TRIGGER security_monitor_history_no_update
    BEFORE UPDATE OR DELETE ON security_monitor_incident_history
    FOR EACH ROW EXECUTE FUNCTION reject_security_monitor_immutable_mutation();
