-- Persist the complete, versioned specialist dispatch input before any worker can claim it.
ALTER TABLE agent_plan_dispatch_outbox
    ADD COLUMN IF NOT EXISTS envelope_version SMALLINT,
    ADD COLUMN IF NOT EXISTS envelope_json JSONB,
    ADD COLUMN IF NOT EXISTS envelope_digest TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='agent_plan_outbox_envelope_all_or_none'
          AND conrelid='agent_plan_dispatch_outbox'::regclass
    ) THEN
        ALTER TABLE agent_plan_dispatch_outbox
            ADD CONSTRAINT agent_plan_outbox_envelope_all_or_none
            CHECK (
                (envelope_version IS NULL AND envelope_json IS NULL AND envelope_digest IS NULL)
                OR
                (envelope_version = 1 AND envelope_json IS NOT NULL
                 AND jsonb_typeof(envelope_json) = 'object'
                 AND octet_length(envelope_json::text) <= 24576
                 AND envelope_digest IS NOT NULL
                 AND envelope_digest ~ '^[0-9a-f]{64}$')
            );
    END IF;
END;
$$;

COMMENT ON COLUMN agent_plan_dispatch_outbox.envelope_json IS
    'Immutable, bounded, versioned dispatch input; NULL rows are not eligible for worker execution.';
COMMENT ON COLUMN agent_plan_dispatch_outbox.envelope_digest IS
    'SHA-256 of canonical UTF-8 JSON for envelope_json, verified by the application before dispatch.';
