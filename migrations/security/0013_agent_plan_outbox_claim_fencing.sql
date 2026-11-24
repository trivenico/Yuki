-- WI-6U14-H: fence each outbox claim attempt, not only its reservation generation.
ALTER TABLE agent_plan_dispatch_outbox
    ADD COLUMN IF NOT EXISTS claim_token TEXT,
    ADD COLUMN IF NOT EXISTS claim_generation BIGINT NOT NULL DEFAULT 0;

-- Existing processing claims cannot be proven live after this migration. Return
-- them to pending and invalidate any former worker before enabling claim fencing.
UPDATE agent_plan_dispatch_outbox
SET state='pending', claim_token=NULL, available_at=clock_timestamp(),
    updated_at=clock_timestamp()
WHERE state='processing';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='agent_plan_outbox_claim_generation_check'
          AND conrelid='agent_plan_dispatch_outbox'::regclass
    ) THEN
        ALTER TABLE agent_plan_dispatch_outbox
            ADD CONSTRAINT agent_plan_outbox_claim_generation_check
            CHECK (claim_generation >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='agent_plan_outbox_claim_token_check'
          AND conrelid='agent_plan_dispatch_outbox'::regclass
    ) THEN
        ALTER TABLE agent_plan_dispatch_outbox
            ADD CONSTRAINT agent_plan_outbox_claim_token_check
            CHECK (claim_token IS NULL OR length(claim_token) >= 32);
    END IF;
END;
$$;
