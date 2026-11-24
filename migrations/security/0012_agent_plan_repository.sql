-- WI-6U14-D: shared PostgreSQL persistence primitives for tenant-owned agent plans.
-- This migration is additive. The coordinator is not switched to these tables until
-- the repository adapter and transaction/concurrency tests are implemented.
CREATE TABLE IF NOT EXISTS agent_plans (
    plan_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    client_idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT agent_plans_identity_nonempty CHECK (
        length(btrim(tenant_id)) BETWEEN 1 AND 200
        AND length(btrim(owner_principal_id)) BETWEEN 1 AND 200
        AND length(btrim(client_idempotency_key)) BETWEEN 1 AND 200
    ),
    CONSTRAINT agent_plans_digest_format CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT agent_plans_tenant_owner_plan_unique
        UNIQUE (tenant_id, owner_principal_id, plan_id),
    CONSTRAINT agent_plans_scoped_idempotency_unique
        UNIQUE (tenant_id, owner_principal_id, client_idempotency_key)
);

CREATE INDEX IF NOT EXISTS agent_plans_owner_created_idx
    ON agent_plans (tenant_id, owner_principal_id, created_at DESC, plan_id);

CREATE TABLE IF NOT EXISTS agent_plan_subtasks (
    tenant_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    description TEXT NOT NULL,
    capabilities_json JSONB NOT NULL,
    permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    dispatch_generation BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, owner_principal_id, plan_id, subtask_id),
    CONSTRAINT agent_plan_subtasks_plan_owner_fk
        FOREIGN KEY (tenant_id, owner_principal_id, plan_id)
        REFERENCES agent_plans (tenant_id, owner_principal_id, plan_id)
        ON DELETE CASCADE,
    CONSTRAINT agent_plan_subtasks_task_owner_fk
        FOREIGN KEY (tenant_id, task_id)
        REFERENCES workflow_runs (tenant_id, run_id),
    CONSTRAINT agent_plan_subtasks_status_check CHECK (
        status IN ('pending','reserved','queued','running','succeeded','failed','cancelled','blocked')
    ),
    CONSTRAINT agent_plan_subtasks_generation_check CHECK (dispatch_generation >= 0),
    CONSTRAINT agent_plan_subtasks_identifiers_check CHECK (
        length(btrim(subtask_id)) BETWEEN 1 AND 80
        AND length(btrim(agent_id)) BETWEEN 1 AND 80
    )
);

CREATE INDEX IF NOT EXISTS agent_plan_subtasks_dispatch_idx
    ON agent_plan_subtasks (tenant_id, owner_principal_id, plan_id, status, subtask_id);

-- A reservation is acquired before TaskRuntime.enqueue is called. The unique
-- parent/subtask key permits only one current reservation across coordinator
-- instances. Expired reservations are reclaimable only with a new generation.
CREATE TABLE IF NOT EXISTS agent_plan_dispatch_reservations (
    tenant_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    reservation_token TEXT NOT NULL,
    dispatch_generation BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    task_id TEXT,
    PRIMARY KEY (tenant_id, owner_principal_id, plan_id, subtask_id),
    CONSTRAINT agent_plan_reservation_subtask_fk
        FOREIGN KEY (tenant_id, owner_principal_id, plan_id, subtask_id)
        REFERENCES agent_plan_subtasks (tenant_id, owner_principal_id, plan_id, subtask_id)
        ON DELETE CASCADE,
    CONSTRAINT agent_plan_reservation_state_check
        CHECK (state IN ('reserved','enqueued','reconciled','abandoned')),
    CONSTRAINT agent_plan_reservation_generation_check CHECK (dispatch_generation > 0),
    CONSTRAINT agent_plan_reservation_expiry_check CHECK (expires_at > reserved_at)
);

CREATE INDEX IF NOT EXISTS agent_plan_reservations_expiry_idx
    ON agent_plan_dispatch_reservations (expires_at)
    WHERE state = 'reserved';

-- Transactional outbox records the intent to enqueue. A dispatcher may retry
-- delivery, while the stable task idempotency key makes enqueue idempotent.
CREATE TABLE IF NOT EXISTS agent_plan_dispatch_outbox (
    outbox_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    dispatch_generation BIGINT NOT NULL,
    task_id TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT agent_plan_outbox_subtask_fk
        FOREIGN KEY (tenant_id, owner_principal_id, plan_id, subtask_id)
        REFERENCES agent_plan_subtasks (tenant_id, owner_principal_id, plan_id, subtask_id)
        ON DELETE CASCADE,
    CONSTRAINT agent_plan_outbox_generation_unique
        UNIQUE (tenant_id, owner_principal_id, plan_id, subtask_id, dispatch_generation),
    CONSTRAINT agent_plan_outbox_state_check
        CHECK (state IN ('pending','processing','delivered','dead_letter')),
    CONSTRAINT agent_plan_outbox_attempts_check CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS agent_plan_dispatch_outbox_ready_idx
    ON agent_plan_dispatch_outbox (available_at, outbox_id)
    WHERE state IN ('pending','processing');

COMMENT ON TABLE agent_plans IS
    'Tenant/owner-scoped agent plans; ownership is immutable and idempotency is scoped to the owner.';
COMMENT ON TABLE agent_plan_dispatch_reservations IS
    'Cross-instance dispatch lease; reservation generation fences stale coordinators.';
COMMENT ON TABLE agent_plan_dispatch_outbox IS
    'Transactional dispatch intent; delivery is at-least-once and relies on stable TaskRuntime idempotency.';
