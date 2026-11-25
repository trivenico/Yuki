-- Preserve the action field included in the canonical agent-plan request digest.
ALTER TABLE agent_plan_subtasks
    ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'emit';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='agent_plan_subtasks_action_check'
          AND conrelid='agent_plan_subtasks'::regclass
    ) THEN
        ALTER TABLE agent_plan_subtasks
            ADD CONSTRAINT agent_plan_subtasks_action_check
            CHECK (length(btrim(action)) BETWEEN 1 AND 80);
    END IF;
END;
$$;
