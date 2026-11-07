# Phase 230 — hierarchical AI engineering teams

**Status:** first durable parent/child plan primitive implemented; integration and distributed orchestration remain incomplete.

## Goal

Yuki should own an engineering goal, create a bounded director plan, delegate work packages to domain leads, and let each lead coordinate specialist tasks. Every result must remain traceable to its parent goal and independently verifiable.

## Implemented in this phase

- Added the `agent_plan_hierarchy` SQLite table.
- Added `SpecialistAgentCoordinator.submit_child_plan(...)`.
- Child plans are durably linked to a parent plan and a specific pending parent subtask.
- Hierarchy depth is calculated from the persisted parent chain and capped at 3.
- Child task permission requirements must be a subset of the parent subtask's declared permission scope.
- Child creation requires the explicit `agent:delegate` authorization action.
- Parent plan ownership is checked; only its owner can create a child plan.
- Child teams must be attached before the parent subtask is dispatched.
- Child creation and local dispatch are serialized through the coordinator's reentrant lock.
- A parent audit event records child plan ID, parent subtask and depth.

## Important limitations

- Parent-child creation and parent dispatch are serialized only within one coordinator instance. A cross-process race still requires a shared transaction boundary spanning parent validation, child plan creation and hierarchy insertion.
- Child-plan completion is not yet wired into the parent task lifecycle. A parent lead task does not automatically wait for all child tasks.
- Permission inheritance is enforced against the parent subtask's declared required permissions. The runtime still independently applies task-level authorization.
- No autonomous recursive agent execution is enabled by this primitive. A model cannot silently create child plans; it must pass the coordinator's authorization gate.
- This is not yet a distributed multi-host scheduler, nor evidence of 100 concurrent agents.

## Next implementation sequence

1. Add parent-child completion aggregation: child plan terminal states, required success criteria, cancellation propagation and parent unblock.
2. Move hierarchy mutations and capacity reservations into one transactional service so multiple coordinator processes cannot race.
3. Add inherited time, token, cost, task-count and output budgets; descendants cannot increase them.
4. Add persisted team roles and versioned manifests for engineering lead, research lead, QA lead, security lead and operations lead.
5. Add director orchestration phases: requirements, architecture, implementation, tests, independent review, release and learning.
6. Add fleet-wide provider and worker capacity scheduling, fair queues and backpressure.
7. Expose team creation, approval, trace, status, cancellation and recovery through the Yuki CLI and desktop app.

## Safety invariants

- Child plans cannot broaden parent permission scope.
- Maximum hierarchy depth is 3 until deeper delegation is threat-modelled and tested.
- Every child plan has an owner, parent plan, parent subtask, depth and audit event.
- Agent-produced content is untrusted input; it cannot override policies or approvals.
- Implementation agents cannot approve their own high-risk work.
- Yuki may autonomously delegate only inside the permissions, budget and policies granted by the user/operator.
