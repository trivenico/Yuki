# Phase 107 — unified agent execution service

## Delivered

- Added `core/agent_execution_service.py` as an application-level facade over existing Yuki subsystems.
- `submit_goal()` invokes model-assisted planning and returns the coordinator's durable plan.
- `get_plan()` reads plan state through the coordinator's authorization boundary without causing dispatch side effects.
- `advance_plan()` explicitly reconciles completed worker task states and dispatches newly dependency-ready subtasks.
- `approve_subtask()` verifies plan membership and dependency readiness, then delegates approval to `WorkflowApprovalService`, which resolves a trusted principal and requires `workflow:approve`.
- After approval, the coordinator is advanced again so dependency-ready work can be reconciled.
- After a worker cycle, callers explicitly invoke `advance_plan()` to reconcile persisted worker results into the plan and unlock downstream dependencies.
- `run_worker_cycle()` delegates to the existing finite `WorkerSupervisor`; the underlying workflow store only leases tasks whose approval requirements are satisfied.
- Constructor validation prevents accidental split-brain wiring: planner and service must share the coordinator; coordinator and supervisor must share the same `TaskRuntime`; approval service and worker runtime must share the same workflow store.
- Added an end-to-end test covering model planning, blocked worker execution before approval, trusted approval, specialist execution, and persisted success.
- Registered the service as an experimental capability.

## Security and execution boundaries

- The model is not an approver and cannot disable approval requirements.
- Caller actor identity is supplied by the authenticated application adapter and independently checked by the coordinator.
- Approval request context is resolved by the trusted principal resolver; request-body actor IDs are not accepted as proof of identity.
- The worker executes only explicitly registered specialist handlers. Existing specialist handlers return bounded text and do not execute shell commands or generated code.
- Worker leases, task events, and completion are persisted through the existing workflow store.
- The service does not create a background daemon; a trusted scheduler or application process must invoke finite worker cycles.

## Validation

```bash
python -m unittest tests.test_agent_execution_service -v
python -m unittest tests.test_model_agent_planner -v
python -m unittest tests.test_specialist_worker -v
python -m unittest tests.test_workflow_approval -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

Require exact-head Linux and Windows CI before merge. This phase is stacked on Phase 106.
