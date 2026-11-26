-- Persist a bounded, non-sensitive classification for fenced permanent dispatch failures.
ALTER TABLE agent_plan_dispatch_outbox
    ADD COLUMN IF NOT EXISTS failure_code TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='agent_plan_outbox_failure_code_check'
          AND conrelid='agent_plan_dispatch_outbox'::regclass
    ) THEN
        ALTER TABLE agent_plan_dispatch_outbox
            ADD CONSTRAINT agent_plan_outbox_failure_code_check
            CHECK (
                failure_code IS NULL
                OR failure_code ~ '^[a-z][a-z0-9_]{0,79}$'
            );
    END IF;
END;
$$;

COMMENT ON COLUMN agent_plan_dispatch_outbox.failure_code IS
    'Bounded non-sensitive permanent dispatch failure classification; never store exception text or secrets.';
