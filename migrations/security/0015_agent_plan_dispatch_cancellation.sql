-- Add durable cancellation tombstones to the PostgreSQL dispatch reservation state machine.
ALTER TABLE agent_plan_dispatch_reservations
    DROP CONSTRAINT IF EXISTS agent_plan_reservation_state_check;

ALTER TABLE agent_plan_dispatch_reservations
    ADD CONSTRAINT agent_plan_reservation_state_check
    CHECK (state IN ('reserved','enqueued','reconciled','abandoned','cancelled'));
