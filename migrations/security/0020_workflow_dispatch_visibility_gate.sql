-- Hold newly-created specialist tasks until their outbox delivery is atomically committed.
ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS dispatch_ready BOOLEAN NOT NULL DEFAULT TRUE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='workflow_runs_dispatch_ready_boolean'
          AND conrelid='workflow_runs'::regclass
    ) THEN
        ALTER TABLE workflow_runs
            ADD CONSTRAINT workflow_runs_dispatch_ready_boolean
            CHECK (dispatch_ready IN (TRUE, FALSE));
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS workflow_runs_dispatch_ready_idx
    ON workflow_runs (created_at, run_id)
    WHERE state='queued' AND dispatch_ready=TRUE;

COMMENT ON COLUMN workflow_runs.dispatch_ready IS
    'False only for a specialist task held until its outbox row and plan binding are committed in the same PostgreSQL transaction.';
