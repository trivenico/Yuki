-- WI-6U13K: tenant-scoped goal and dependency metadata for distributed orchestration.
-- Workflow runs remain the authoritative execution records; these tables only
-- group runs into goals and persist dependency relationships and goal events.
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
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES workflow_runs(tenant_id, run_id)
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
