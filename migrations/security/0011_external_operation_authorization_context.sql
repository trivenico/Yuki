-- WI-6U13J-C: bind journal rows to the verified workload and task lease
-- that authorized the intent. Legacy rows remain explicitly unbound.
ALTER TABLE external_operations
    ADD COLUMN IF NOT EXISTS agent_id TEXT,
    ADD COLUMN IF NOT EXISTS task_lease_generation BIGINT,
    ADD COLUMN IF NOT EXISTS authorization_decision_id TEXT,
    ADD COLUMN IF NOT EXISTS policy_revision TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'external_operations_authorization_context_check'
          AND conrelid = 'external_operations'::regclass
    ) THEN
        ALTER TABLE external_operations
            ADD CONSTRAINT external_operations_authorization_context_check
            CHECK (
                (agent_id IS NULL AND task_lease_generation IS NULL
                 AND authorization_decision_id IS NULL AND policy_revision IS NULL)
                OR
                (agent_id IS NOT NULL AND length(agent_id) BETWEEN 1 AND 200
                 AND task_lease_generation IS NOT NULL AND task_lease_generation >= 0
                 AND authorization_decision_id IS NOT NULL
                 AND length(authorization_decision_id) BETWEEN 1 AND 200
                 AND policy_revision IS NOT NULL AND length(policy_revision) BETWEEN 1 AND 128)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS external_operations_authorized_claim_idx
    ON external_operations
       (tenant_id, agent_id, task_id, action_name, task_lease_generation, created_at, operation_id)
    WHERE state = 'pending';
