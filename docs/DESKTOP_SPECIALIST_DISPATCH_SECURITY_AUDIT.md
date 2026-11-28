# Desktop specialist dispatch security audit

**Status:** source-traced and partially remediated on the integration branch. This is not approval to enable background specialist execution.

## Verified desktop path

1. `ui/desktop/launch.py::_build_services()` constructs authentication and `WorkflowRunStore` through `build_control_plane_stores()`, which owns the private profile's `MaintenanceFence`.
2. The desktop creates `TaskRuntime` with an authenticated permission callback and `SpecialistAgentCoordinator` over the same `tasks.sqlite3` file.
3. `CompanionApplicationService.propose_agent_task()` asks the selected provider for a bounded JSON task decomposition. The parser rejects extra task fields and unconditionally sets `requires_approval=True`; provider output cannot opt a task out of approval.
4. `SpecialistAgentCoordinator.submit()` persists a proposal. `advance()`, reached through the explicit desktop dispatch action, creates durable workflow tasks; it does not execute specialist handlers.
5. The desktop task-review dialog calls `approve_task()` for the exact displayed task version. `WorkflowRunStore.approve_with_event()` records the current session hash and a bounded expiry in the same transaction as the approval event.
6. `claim_next()` filters out tasks that require approval until approval metadata is present and unexpired, and also excludes tasks whose approving session has been revoked.
7. The native desktop startup does not construct `SpecialistTaskWorker`, `build_provider_specialist_handlers()`, or a worker supervisor. The approval UI explicitly reports that approval does not start a worker. Thus the traced desktop path queues and approves tasks but does not currently execute them.

## Finding fixed: coordinator bypassed the desktop backup fence

The coordinator stores `agent_plans`, `agent_plan_subtasks`, and `specialist_agents` in the same `tasks.sqlite3` database used by `WorkflowRunStore`. Before this change, its private `_connect()` opened SQLite directly and did not acquire the profile maintenance lease. A plan/registry transaction could therefore overlap a desktop backup even though workflow-store operations were fenced.

Changes:
- `SpecialistAgentCoordinator` accepts the host's explicit `MaintenanceFence`.
- When supplied, it validates that its database is directly beneath that fence's profile root.
- It acquires the lease before opening SQLite and retains it through transaction commit/rollback and connection close.
- Desktop `_build_services()` injects the exact fence returned by `build_control_plane_stores()`.
- Regression tests cover lease participation and rejection of a database outside the fenced profile.

The optional constructor argument preserves compatibility for standalone/test compositions. Such unfenced compositions are not covered by the desktop backup guarantee; production hosts must inject the canonical fence. The fence coordinates only cooperating processes and does not make separate SQLite databases atomic.

## Remaining security boundaries

- The separate guarded approval gateway/composition is still not wired into this specialist workflow. Specialist tasks use the existing workflow-store approval mechanism; do not describe the two approval systems as unified.
- The desktop currently has no specialist worker. Do not add one as a background convenience until worker lifecycle, trusted service identity, session revocation at claim/dispatch, durable outcome handling, and shutdown/backup coordination are reviewed together.
- `SpecialistTaskWorker` is a text-only provider handler today: it does not run shell commands, generated code, or arbitrary workspace writes. That is a useful restriction, not a sandbox.
- Other standalone coordinator constructors remain possible. A future production host must pass the canonical fence and must not share this database with unfenced writers.
- Model/provider output and prior specialist results remain untrusted text. They must never be interpreted as executable commands or authority.
- The in-process Python object graph is not a security sandbox. Never pass privileged stores, session-token providers, handler registries, or approval gateways into model-controlled context.

## Readiness decision

The desktop specialist plan tables now participate in the same profile maintenance fence as workflow-store operations and backup. This closes the identified cooperating-writer race for the native desktop composition. It does **not** establish application-wide quiescence, unify approval ledgers, activate a worker, or authorize consequential host actions.

Tests were authored but not run locally. CI status must be checked against the latest branch head. No merge or deployment.
