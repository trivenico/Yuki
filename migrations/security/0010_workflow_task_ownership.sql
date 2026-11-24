-- WI-6U13J-A: trusted ownership metadata for durable workflow tasks.
-- Historical rows remain explicitly unassigned; no tenant is inferred or defaulted.
ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS tenant_id TEXT,
    ADD COLUMN IF NOT EXISTS owner_principal_id TEXT,
    ADD COLUMN IF NOT EXISTS ownership_status TEXT NOT NULL DEFAULT 'legacy_unassigned';

ALTER TABLE workflow_runs
    ADD CONSTRAINT workflow_runs_ownership_status_check
    CHECK (
        (ownership_status = 'legacy_unassigned' AND tenant_id IS NULL AND owner_principal_id IS NULL)
        OR
        (ownership_status = 'owned' AND tenant_id IS NOT NULL AND owner_principal_id IS NOT NULL)
    );

ALTER TABLE workflow_runs
    ADD CONSTRAINT workflow_runs_tenant_run_unique UNIQUE (tenant_id, run_id);

-- Enforce that an external operation's tenant is the tenant of its parent task.
-- NOT VALID preserves legacy rows but enforces this relationship for every new write.
ALTER TABLE external_operations
    ADD CONSTRAINT external_operations_tenant_task_fk
    FOREIGN KEY (tenant_id, task_id)
    REFERENCES workflow_runs (tenant_id, run_id)
    NOT VALID;

CREATE INDEX IF NOT EXISTS workflow_runs_tenant_owner_created_idx
    ON workflow_runs(tenant_id, owner_principal_id, created_at DESC)
    WHERE ownership_status = 'owned';

-- Ownership is immutable after task creation; reassignments require a separately
-- designed audited transfer workflow rather than a generic row update.
CREATE OR REPLACE FUNCTION yuki_reject_workflow_ownership_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.owner_principal_id IS DISTINCT FROM OLD.owner_principal_id
       OR NEW.ownership_status IS DISTINCT FROM OLD.ownership_status THEN
        RAISE EXCEPTION 'workflow_task_ownership_is_immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER workflow_task_ownership_immutable
BEFORE UPDATE OF tenant_id, owner_principal_id, ownership_status
ON workflow_runs
FOR EACH ROW EXECUTE FUNCTION yuki_reject_workflow_ownership_change();
