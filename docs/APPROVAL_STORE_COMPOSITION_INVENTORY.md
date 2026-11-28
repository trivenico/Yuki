# Approval store composition and writer inventory

## Status and scope

**Source-trace snapshot for PR #1346. Not an enablement or migration approval.**

This inventory follows the current PR head's approval implementation, the native desktop startup composition, the authenticated approval facade, and the approval-related PR patches/tests. It distinguishes production composition from test fixtures. It does not claim a complete repository-wide call graph: GitHub code search indexes the default branch, not this PR head, so absence claims below are limited to the inspected entrypoints and PR change set.

## Verified production composition

### Native desktop startup

- `ui/desktop/launch.py::_build_services` calls `build_control_plane_stores(profile_root=root, auth_database_name="auth.sqlite3", workflow_database_name="tasks.sqlite3")` (current branch file around lines 173–220).
- `core/control_plane_stores.py::build_control_plane_stores` creates one `MaintenanceFence(root)`, then injects that exact object into `BuiltInAuthService` and `WorkflowRunStore`. It fixes both database paths beneath the validated private profile and rejects caller-supplied paths/fences.
- The desktop startup then creates `DesktopSessionManager`, `TaskRuntime`, and `SpecialistAgentCoordinator` around the auth/workflow stores. The inspected composition does not construct `SQLitePendingApprovalStore`, `SQLiteApprovalAuditLog`, `SQLiteToolApprovalStore`, `ApprovalCoordinator`, `GuardedApprovalExecutor`, or `AuthenticatedApprovalGateway`.
- The desktop task runtime's visible production path is the authenticated workflow/task store; it is not evidence that the separate guarded approval workflow is active.

### Approval components

- `core/approval_flow.py::ApprovalCoordinator` accepts pending/audit stores and an optional executor. It enforces object-identity equality for supplied store fences and holds the fence around record/approve when configured. It can still be constructed without a fence when its stores are also unfenced.
- `core/guarded_approval_executor.py::GuardedApprovalExecutor` requires a typed `SQLiteToolApprovalStore`, an append-capable audit sink, a live identity validator, explicit non-empty handler registry, and matching grant/audit fence object identity. It issues one-shot grants through `SQLiteToolApprovalStore.issue()`.
- `core/one_shot_tool_approval_policy.py::OneShotToolApprovalPolicy` consumes the one-shot grant and appends the policy-level approved event.
- `core/authenticated_approval_gateway.py::AuthenticatedApprovalGateway` binds requests/confirmation to a live `BuiltInAuthService` session and permissions, but its constructor requires an already-built coordinator; it is a facade, not a composition root.
- `core/approval_recovery.py::scan_approval_recovery` is diagnostic-only. The CLI consistency-check path opens existing stores read-only for an explicit owner-run scan; this is not a production approval writer or dispatch composition.

## Writer and constructor evidence

The approval PR change set contains production definitions for the three SQLite stores and the coordinator/executor/gateway, but constructor callsites found in its approval-related patches are test fixtures:

- `tests/test_authenticated_approval_gateway.py` builds auth, pending, audit, grant stores, executor, coordinator, and gateway directly. Its setup does not inject a shared maintenance fence.
- `tests/test_guarded_approval_executor.py` includes an end-to-end fixture and a dedicated test proving that a deliberately composed pending/audit/grant/coordinator set can share one `MaintenanceFence`. The default fixture constructs audit/grant stores without a fence.
- `tests/test_approval_coordinator_security.py`, `tests/test_approval_flow.py`, `tests/test_one_shot_tool_approval_policy.py`, and `tests/test_tool_action_approvals.py` construct stores/coordinators for isolated tests.
- No production callsite for `ApprovalCoordinator(`, `GuardedApprovalExecutor(`, or `AuthenticatedApprovalGateway(` was found in the inspected desktop startup or the approval-related PR patches. This is a bounded finding, not proof that no external consumer or uninspected entrypoint exists.

Store-level write APIs remain callable by any code holding their instances:
- `SQLitePendingApprovalStore.put/consume/deny` mutate pending state.
- `SQLiteApprovalAuditLog.append` mutates the audit chain.
- `SQLiteToolApprovalStore.issue/consume/revoke` mutate one-shot grants.
- `GuardedApprovalExecutor` is the intended trusted grant-issuance composition; direct `issue()` access must not be exposed to model-controlled or untrusted callers.
- Optional `maintenance_fence=None` on individual store constructors means standalone construction does not itself guarantee cross-process maintenance coordination. Coordinator/executor identity checks only help when a trusted composition actually injects a common object.

## Fence and transaction conclusions

1. The desktop auth/workflow fence is verified for the inspected native desktop startup. It does not automatically cover the separate approval stores.
2. The approval classes can be assembled with a shared fence, and a test demonstrates that shape. No production approval composition was identified in the inspected path.
3. The read-only recovery CLI is a separate diagnostic composition; it does not create or repair the production writer graph.
4. Even if every approval store shares one `MaintenanceFence`, three separate SQLite databases still do not form one atomic transaction. The fence serializes cooperating operations only.
5. The authenticated gateway re-resolves the session for request/confirm, and the guarded executor has a second live-identity validator. These controls are only effective when a production host wires the same trusted auth/session source into them.

## Blockers before unified-ledger implementation or consequential dispatch

- Identify the intended production host/entrypoint for approval requests and confirmations. If no host should expose this feature yet, record it as deliberately uncomposed and keep it disabled.
- Create one typed approval composition root that owns the auth service, pending store, audit log, grant store, executor, coordinator, gateway, handler registry, identity validator, and one private-profile maintenance fence.
- Ensure all three approval stores receive the exact same fence and are rooted beneath the validated private profile; reject unfenced or path-overridden construction in production composition.
- Inventory every direct writer and every holder of `SQLiteToolApprovalStore.issue()`; restrict grant issuance to the trusted executor boundary.
- Prove session revocation and policy revalidation immediately before handler dispatch, not merely at request/confirmation entry.
- Establish lifecycle ownership and shutdown behavior for these resources; include them in backup/restore consistency and read-only recovery.
- Only then freeze migration mapping and implement the single-database ledger/outbox. Preserve the rule that ambiguous external side effects are never automatically replayed.

## Validation state

- Source inspection only; no local repository execution.
- Tests referenced above are authored in the repository; this inventory does not claim they were run.
- No production data was read or migrated. No approval database schema/path was changed. No consequential tool was enabled.

## Follow-up implementation: explicit approval composition factory

After the source-trace snapshot, `core/approval_composition.py::build_approval_services` was added on this branch. It is a composition factory, not a host integration:

- Requires an existing `BuiltInAuthService`, exact shared `MaintenanceFence`, profile root matching the fence and auth database parent, trusted session-token provider, non-empty handler registry, and one target scope per handler.
- Constructs pending, audit, and one-shot grant databases beneath that profile root and injects the exact same fence into all three.
- Constructs the live-session validator, guarded executor, durable coordinator, and authenticated gateway as one returned immutable service bundle.
- Rechecks store fence identity and database parent paths before returning.
- Does not add a caller in desktop startup, CLI, scheduler, or worker. No handlers are registered by default and no capability is enabled by this change.

This closes the missing *construction API* for a trusted host but does not close the *production integration* or all-writer audit. The factory is only safe when the host passes its canonical auth/fence objects and keeps the returned grant store and executor private. Direct store APIs still exist for tests and maintenance; search/holder review remains a prerequisite before consequential dispatch.

A focused test module was added at `tests/test_approval_composition.py` for common fence/profile invariants, mismatched fence/root rejection, required handler scopes, and session-bound request creation. These tests are authored but have not been executed locally.
