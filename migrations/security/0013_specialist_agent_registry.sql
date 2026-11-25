-- Shared, platform-level specialist routing manifests.
-- This registry stores metadata only; it does not authorize tool execution or load code.
CREATE TABLE IF NOT EXISTS specialist_agent_registry (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capabilities_json JSONB NOT NULL,
    action TEXT NOT NULL,
    permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    manifest_revision BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT specialist_agent_registry_id_check
        CHECK (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$'),
    CONSTRAINT specialist_agent_registry_name_check
        CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    CONSTRAINT specialist_agent_registry_action_check
        CHECK (action = 'emit'),
    CONSTRAINT specialist_agent_registry_capabilities_check
        CHECK (
            jsonb_typeof(capabilities_json) = 'array'
            AND jsonb_array_length(capabilities_json) BETWEEN 1 AND 32
        ),
    CONSTRAINT specialist_agent_registry_permissions_check
        CHECK (
            jsonb_typeof(permissions_json) = 'array'
            AND jsonb_array_length(permissions_json) <= 32
        ),
    CONSTRAINT specialist_agent_registry_revision_check
        CHECK (manifest_revision > 0)
);

CREATE INDEX IF NOT EXISTS specialist_agent_registry_enabled_idx
    ON specialist_agent_registry (agent_id)
    WHERE enabled;

COMMENT ON TABLE specialist_agent_registry IS
    'Global specialist routing manifests; metadata only, not executable code or authorization grants.';
