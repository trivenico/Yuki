-- Preserve the action field included in the canonical agent-plan request digest.
ALTER TABLE agent_plan_subtasks
    ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'emit';

ALTER TABLE agent_plan_subtasks
    ADD CONSTRAINT agent_plan_subtasks_action_check
    CHECK (length(btrim(action)) BETWEEN 1 AND 80);
