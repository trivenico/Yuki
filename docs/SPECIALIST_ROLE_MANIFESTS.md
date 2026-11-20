# Versioned specialist role manifests

The coordinator's `specialist_agents` table remains the canonical registry. This feature extends its existing records; it does not create a second registry or task queue.

## Manifest fields

Each specialist record now carries:
- `department`, `role_version`, and `owner` for organizational identity and accountability;
- `handler_id` and `handler_ready` to distinguish a role description from a reviewed executable handler;
- `max_steps` as an enforced per-plan subtask ceiling and `max_tokens` as a declared ceiling pending provider support;
- `approval_class` (`read_only`, `review_required`, or `human_approval`).

The coordinator rejects routing to a specialist unless it is both enabled and handler-ready. Legacy records are migrated to the existing `emit` handler to preserve their current behavior.

## Starter roles

`automation/agent_roles.py` defines six starter slots:
- Chief of Staff (`EXEC-01`)
- Research Analyst (`RES-12`)
- Software Engineer (`ENG-03`)
- QA / Evals Engineer (`ENG-16`)
- Security Reviewer (`SEC-02`)
- Technical Writer (`PROD-09`)

They are created disabled, with no permissions, no handler, and `handler_ready=False`. `register_initial_role_slots` persists them only when called by an authorized coordinator actor. They cannot be routed or dispatched.

## Important limits

This is registry and governance groundwork, not six functioning agents. The current specialist worker only supports the bounded `emit` action and the existing text-provider adapters; it does not execute arbitrary tools or generated code. `max_steps` is enforced as the maximum number of subtasks assigned to one specialist in a single plan. It is not a reasoning-step, concurrency, or wall-clock limit. `max_tokens` remains metadata because the current provider adapter contract does not expose a verifiable per-request token ceiling. Approval class is enforced during planning: `review_required` and `human_approval` force task approval, while `read_only` preserves the caller's explicit choice. The existing task runtime still owns authenticated approval and session checks. A role must remain disabled until a concrete deployment binding, enforcement path, tests, and review are implemented.

Do not set `handler_ready=True` merely to make a role routable. Handler readiness is a release gate, not an administrative convenience.

## Existing provider-backed handler adapters

The existing `automation.specialist_worker` already provides bounded text-only provider handlers for research, coding, testing, and reporting. `build_initial_role_handlers(provider_manager)` maps those implementations to RES-12, ENG-03, ENG-16, and PROD-09 and returns the mapping expected by `SpecialistTaskWorker`.

This is an adapter factory, not activation. It does not register handlers in the coordinator, change permissions, enable roles, or set `handler_ready=True`. Chief of Staff and Security Reviewer do not yet have matching role-specific implementations in this adapter map. All six role records remain disabled until a separately reviewed deployment composition wires the provider, worker, task authorization, and lifecycle controls together.

For the starter provider roles, use `build_initial_role_worker(runtime, coordinator, provider_manager, worker_id=..., service_actor_id=...)`. This composition helper builds the four text-only adapters and always injects `coordinator.validate_task_manifest` into `SpecialistTaskWorker`. It rejects a coordinator bound to a different `TaskRuntime`. It does not register, enable, or mark any role ready; activation remains a separate reviewed operation. The existing `TaskRuntime` authorization callbacks continue to enforce task permissions.

## Plan audit snapshots

Every newly created delegated subtask now records the selected specialist's `role_version` and `handler_id` alongside its assigned agent ID. This preserves which manifest revision was selected even if the live registry is later updated. Existing plan rows migrate with version 1 and a null handler snapshot because the earlier schema did not capture these fields.

New delegated subtasks also snapshot the selected action. Before task creation, dispatch compares the current registry's role version, handler ID, and action with the plan snapshot. If any of these changed while the plan was waiting, that subtask is marked failed rather than silently running against a different handler revision. Historical rows migrated without a snapshot remain null and therefore fail closed; create a fresh plan for those rows.

## Approval-class enforcement

At plan creation, `review_required` and `human_approval` roles cannot be dispatched as unapproved tasks, even if the caller sets `requires_approval=False`. A `read_only` role may use the caller's explicit approval choice. All approvals still flow through the existing task runtime's authenticated approval/session mechanism; the role class does not create a bypass or a new approval authority.

The `max_steps` ceiling is enforced at plan creation as the maximum number of subtasks assigned to one specialist in a single plan. It is not a model reasoning-step or wall-clock budget. `max_tokens` remains declarative until the provider adapter exposes a verifiable token-limit contract; no token-budget enforcement is claimed yet.

Approval class is also stored in the plan snapshot and compared at dispatch. This makes dispatch fail closed if an approval policy is changed in the registry without a corresponding role-version bump.

## Worker claim-time manifest binding

Coordinator-created tasks are marked as manifest-bound. Before enqueue, the coordinator persists a dispatch intent containing the bounded idempotency key, task name, and canonical task-definition digest. The worker requires an injected validator and fails closed if the validator is missing, raises, or reports a mismatch. At claim time, the validator verifies the runtime task against that intent and atomically binds its task ID to the plan row if the worker wins the race against the coordinator's post-enqueue update. The same intent supports recovery after a coordinator restart. It then checks role version, handler ID, action, approval class, enabled state, and handler readiness. The worker repeats the check after the provider handler returns and before accepting its output, so a role revoked or revised during execution cannot publish a stale result. Late coordinator updates cannot regress a running task to queued or overwrite a terminal state; a running task can still advance to its observed terminal result. Tests cover unchanged and changed roles, missing validators, claim-before-bind ordering, task-definition tampering, mid-execution manifest changes, and monotonic status reconciliation.

This is an execution-integrity guard, not an OS sandbox or a provider-code identity attestation. Deployment must inject the coordinator's validator into every specialist worker; legacy unmarked task names remain compatible and do not receive this new guard. The initial role slots remain disabled until reviewed deployment wiring and the remaining permissions/budget/lifecycle controls are complete.

## Deployment contract

Every worker that can claim coordinator-created tasks must be constructed with `manifest_validator=coordinator.validate_task_manifest`. New task names include a manifest marker; the worker fails these tasks if the validator is absent or fails. This validator confirms the task-to-plan association and that the snapshotted role version, handler ID, action, and approval class still match an enabled, handler-ready registry row. The `handler_id` currently identifies the permitted `emit` execution contract, not a cryptographic identity for provider code. Provider implementation provenance and per-role token enforcement are separate future controls.

Dispatch task names are kept below the task runtime's 100-character limit even when an agent ID is at its maximum supported length. Runtime idempotency keys use the fixed-length plan ID rather than concatenating the caller's potentially 200-character key.
