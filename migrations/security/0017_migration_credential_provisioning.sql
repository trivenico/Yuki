-- Secret-free durable ledger for the PostgreSQL migration credential saga.
-- Secret material remains in the external secret manager; only immutable
-- opaque reference/version metadata is stored here.
CREATE TABLE IF NOT EXISTS migration_credential_provisioning (
    attempt_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL CHECK (length(target_id) BETWEEN 1 AND 256),
    login_role TEXT NOT NULL UNIQUE
        CHECK (login_role ~ '^[a-z][a-z0-9_]{0,62}$'),
    state TEXT NOT NULL
        CHECK (state IN ('reserved','secret_issued','credential_applied','verified','retiring','retired')),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    secret_reference TEXT,
    credential_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((secret_reference IS NULL) = (credential_version IS NULL)),
    CHECK (
        state NOT IN ('secret_issued','credential_applied','verified')
        OR (secret_reference IS NOT NULL AND credential_version IS NOT NULL)
    ),
    CHECK (secret_reference IS NULL OR length(secret_reference) BETWEEN 1 AND 1024),
    CHECK (credential_version IS NULL OR length(credential_version) BETWEEN 1 AND 256),
    CHECK (updated_at >= created_at)
);

CREATE INDEX IF NOT EXISTS idx_migration_credential_recovery
    ON migration_credential_provisioning (updated_at, attempt_id)
    WHERE state <> 'retired';

CREATE INDEX IF NOT EXISTS idx_migration_credential_state_recovery
    ON migration_credential_provisioning (state, updated_at, attempt_id)
    WHERE state <> 'retired';

CREATE OR REPLACE FUNCTION yuki_guard_migration_credential_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.attempt_id <> OLD.attempt_id
       OR NEW.target_id <> OLD.target_id
       OR NEW.login_role <> OLD.login_role THEN
        RAISE EXCEPTION 'migration credential identity binding is immutable'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'migration credential version must advance exactly once'
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.secret_reference IS NOT NULL
       AND (NEW.secret_reference IS DISTINCT FROM OLD.secret_reference
            OR NEW.credential_version IS DISTINCT FROM OLD.credential_version) THEN
        RAISE EXCEPTION 'migration credential reference and version are immutable'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NOT (
        (OLD.state = 'reserved' AND NEW.state IN ('secret_issued','retiring'))
        OR (OLD.state = 'secret_issued' AND NEW.state IN ('credential_applied','retiring'))
        OR (OLD.state = 'credential_applied' AND NEW.state IN ('verified','retiring'))
        OR (OLD.state = 'verified' AND NEW.state = 'retiring')
        OR (OLD.state = 'retiring' AND NEW.state = 'retired')
    ) THEN
        RAISE EXCEPTION 'invalid migration credential state transition: % -> %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.state = 'secret_issued' AND OLD.secret_reference IS NULL
       AND (NEW.secret_reference IS NULL OR NEW.credential_version IS NULL) THEN
        RAISE EXCEPTION 'secret issuance requires an opaque reference and version'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'migration credential updated_at cannot move backwards'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_migration_credential_transition
    ON migration_credential_provisioning;
CREATE TRIGGER trg_guard_migration_credential_transition
    BEFORE UPDATE ON migration_credential_provisioning
    FOR EACH ROW EXECUTE FUNCTION yuki_guard_migration_credential_transition();

COMMENT ON TABLE migration_credential_provisioning IS
    'Secret-free, versioned migration credential saga ledger; secret bytes remain external.';
COMMENT ON COLUMN migration_credential_provisioning.login_role IS
    'Unique PostgreSQL LOGIN name reservation; uniqueness is cluster-wide for this control-plane database.';
COMMENT ON COLUMN migration_credential_provisioning.secret_reference IS
    'Opaque external secret-manager reference only; never credential material.';
