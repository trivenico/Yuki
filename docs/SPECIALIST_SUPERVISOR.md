# Specialist supervisor orchestration

`automation.specialist_supervisor.SpecialistSupervisor` composes Yuki's
existing durable `SpecialistAgentCoordinator` and permission-checked
`AgentMessageBus`. It is a small orchestration facade, not a new task engine.

## Example

```python
supervisor = SpecialistSupervisor(
    supervisor_id="supervisor",
    coordinator=coordinator,
    message_bus=message_bus,
)

plan = supervisor.create_plan(
    actor_id=authenticated_user_id,
    idempotency_key="project-42-plan-v1",
    subtasks=(
        SubtaskSpec("research", "Collect evidence", ("research",)),
        SubtaskSpec(
            "verify", "Review evidence", ("testing",),
            depends_on=("research",),
        ),
    ),
)

supervisor.notify_assignments(
    actor_id=authenticated_user_id, plan_id=plan.plan_id,
)
supervisor.dispatch(actor_id=authenticated_user_id, plan_id=plan.plan_id)
```

## Responsibilities

- Submit a bounded, capability-routed plan through coordinator authorization.
- Send durable, idempotent assignment notices through message-bus ACLs.
- Dispatch only through the existing dependency-aware coordinator.
- Read status and cancel through existing coordinator/runtime boundaries.

## Security and execution boundaries

- The authenticated user actor is passed unchanged to coordinator methods.
- The supervisor's service identity must be registered in the message bus.
- Message-bus sender and recipient ACLs remain authoritative.
- Assignment notices are untrusted metadata, not execution grants.
- Worker handlers, approval policy, task leases, and runtime authorization are
  unchanged.
- This facade does not call models, invent plans, execute tools, run code,
  create worker threads, or start background loops.
- Notification delivery is idempotent per plan/subtask; a partial failure can
  be retried safely. Notifications are advisory; task runtime state is the
  source of truth.

The application remains responsible for constructing trusted authorization
callbacks, registering the supervisor identity, worker lifecycle, and human
approval for sensitive actions.

## Tests

```bash
python -m unittest tests.test_specialist_supervisor
```
