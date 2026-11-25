-- WI-6U13K: tenant-scoped goal and dependency metadata for distributed orchestration.
-- Workflow runs remain the authoritative execution records; these tables only
-- group runs into goals and persist dependency relationships and goal events.
CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_tenant_run_owner_unique
    ON workflow_runs(tenant_id, run_id, owner_principal_id);

CREATE TABLE IF NOT EXISTS workflow_goals (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 200),
    goal_id TEXT NOT NULL CHECK (length(goal_id) BETWEEN 1 AND 256),
    owner_principal_id TEXT NOT NULL CHECK (length(owner_principal_id) BETWEEN 1 AND 200),
    state TEXT NOT NULL CHECK (state IN ('active','paused','cancelled','completed')),
    outcome TEXT CHECK (outcome IS NULL OR outcome IN ('succeeded','failed','cancelled','mixed')),
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, goal_id),
    UNIQUE (tenant_id, goal_id, owner_principal_id)
);

CREATE TABLE IF NOT EXISTS workflow_goal_tasks (
    tenant_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, goal_id, task_id),
    UNIQUE (tenant_id, task_id),
    FOREIGN KEY (tenant_id, goal_id, owner_principal_id)
        REFERENCES workflow_goals(tenant_id, goal_id, owner_principal_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, task_id, owner_principal_id)
        REFERENCES workflow_runs(tenant_id, run_id, owner_principal_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS workflow_goal_tasks_goal_idx
    ON workflow_goal_tasks(tenant_id, goal_id, created_at, task_id);

CREATE TABLE IF NOT EXISTS workflow_goal_dependencies (
    tenant_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, goal_id, task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id),
    FOREIGN KEY (tenant_id, goal_id, task_id)
        REFERENCES workflow_goal_tasks(tenant_id, goal_id, task_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, goal_id, depends_on_task_id)
        REFERENCES workflow_goal_tasks(tenant_id, goal_id, task_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS workflow_goal_dependencies_prerequisite_idx
    ON workflow_goal_dependencies(tenant_id, goal_id, depends_on_task_id);

CREATE TABLE IF NOT EXISTS workflow_goal_events (
    event_id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    task_id TEXT,
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 96),
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(detail_json) = 'object' AND octet_length(detail_json::text) <= 8192),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_id, goal_id, owner_principal_id)
        REFERENCES workflow_goals(tenant_id, goal_id, owner_principal_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, goal_id, task_id)
        REFERENCES workflow_goal_tasks(tenant_id, goal_id, task_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS workflow_goal_events_cursor_idx
    ON workflow_goal_events(tenant_id, goal_id, event_id);

-- Goal ownership is immutable just like workflow-run ownership. A future
-- ownership transfer must be a separately reviewed, audited operation.
CREATE OR REPLACE FUNCTION yuki_reject_workflow_goal_ownership_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.goal_id IS DISTINCT FROM OLD.goal_id
       OR NEW.owner_principal_id IS DISTINCT FROM OLD.owner_principal_id THEN
        RAISE EXCEPTION 'workflow_goal_ownership_is_immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER workflow_goal_ownership_immutable
BEFORE UPDATE OF tenant_id, goal_id, owner_principal_id
ON workflow_goals
FOR EACH ROW EXECUTE FUNCTION yuki_reject_workflow_goal_ownership_change();

-- Goal events are an append-only history. Corrections are represented by a
-- new event rather than rewriting or deleting prior evidence.
CREATE OR REPLACE FUNCTION yuki_reject_workflow_goal_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'workflow_goal_events_are_append_only'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER workflow_goal_events_append_only
BEFORE UPDATE OR DELETE ON workflow_goal_events
FOR EACH ROW EXECUTE FUNCTION yuki_reject_workflow_goal_event_mutation();
