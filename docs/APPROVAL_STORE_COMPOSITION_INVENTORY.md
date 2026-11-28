# Approval store composition and writer inventory

## Status and scope

**Source-trace snapshot for PR #1346. Not an enablement or migration approval.**

This inventory follows the current PR head's approval implementation, the native desktop startup composition, the authenticated approval facade, and the approval-related PR patches/tests. It distinguishes production composition from test fixtures. It does not claim a complete repository-wide call graph: GitHub code search indexes the default branch, not this PR branch, so absence claims below are limited to the inspected entrypoints and PR change set.

## Verified production composition

### Native desktop startup

- `ui/desktop/launch.py::_build_services` calls `build_control_plane_stores(profile_root=root, auth_database_name="auth.sqlite3", workflow_database_name="tasks.sqlite3")`.
- `core/control_plane_stores.py::build_control_plane_stores` creates one `MaintenanceFence(root)`, then injects that exact object into `BuiltInAuthService` and `WorkflowRunStore`.
- The inspected desktop composition does not construct the guarded approval subsystem. No specialist host is authorized to dispatch through this subsystem yet.

### Approval components

- `ApprovalCoordinator` owns pending request consumption and durable audit sequencing.
- `GuardedApprovalExecutor` accepts only an explicit handler registry. A model-proposed `skill_id` is a lookup key, never an import path.
- `AuthenticatedApprovalGateway` re-resolves the current session and permission at request and confirmation entry.
- The executor's identity validator checks the session again at guarded runtime invocation.
- The CLI recovery scanner is diagnostic-only and never dispatches or retries actions.

## Writer and capability inventory

Store-level APIs remain callable by code holding their instances:

- `SQLitePendingApprovalStore.put/consume/deny` mutate pending state.
- `SQLiteApprovalAuditLog.append` mutates the audit chain.
- `SQLiteToolApprovalStore.issue/consume/revoke` mutate one-shot grants.
- `GuardedApprovalExecutor` is the intended trusted grant-issuance path; direct `issue()` access must never be exposed to model-controlled or untrusted callers.

The `ApprovalServices` bundle deliberately exposes only the authenticated gateway, maintenance-fence identity, and profile root. It does not publish raw pending/audit/grant stores, coordinator, or executor as public dataclass fields. The composition factory now independently calls `ensure_private_profile_directory` before creating approval stores and rejects a symlinked authentication database path. Regression tests cover permissive profile modes and symlinked auth DB paths.

This is least-authority API design, not a security boundary: Python callers in the same process can use introspection, import modules, or reach conventionally private attributes. Never pass the bundle, gateway, coordinator, executor, store objects, handler registry, or session-token provider into a model context or untrusted tool runtime.

The gateway itself is intended only for trusted host/UI code. Its private coordinator reference is not an adversarial isolation mechanism. A process compromise or arbitrary Python execution in the Yuki process defeats these in-process boundaries.

## Search coverage and limits

GitHub code search was queried for `SQLiteToolApprovalStore`, `SQLitePendingApprovalStore`, `GuardedApprovalExecutor`, `build_approval_services`, and `AuthenticatedApprovalGateway`. The connector searches the repository default branch, not this PR branch, and returned no matches for these terms. Those empty results are not evidence that the PR branch has no callsites. The branch-specific review therefore relies on fetched PR-head source files and the documented desktop entrypoint; a complete call graph still requires a branch-aware repository checkout or equivalent indexed source scan.

The inspected approval source shows direct store constructors and writer methods in implementation and tests. No verified production host callsite for the guarded approval graph was found in the inspected desktop startup. This is a bounded finding, not proof that no external consumer or uninspected entrypoint exists.

## Fence and transaction conclusions

1. The desktop auth/workflow fence does not automatically cover separate approval stores.
2. The approval factory injects one exact fence into pending, audit, and grant stores, independently validates the owner-private profile, rejects a symlinked auth DB path, and checks approval database parents beneath the canonical profile root.
3. A shared fence coordinates only cooperating operations. Three SQLite databases are not one atomic transaction.
4. The gateway re-resolves the session at request/confirmation, and the executor has a second live-identity validator. These controls are effective only when a trusted host supplies the actual current session token and keeps all privileged objects private.
5. The grant store's `issue()` method remains a powerful capability. The public service bundle no longer returns it, but Python same-process access is not a sandbox.

## Blockers before consequential dispatch

- Identify and review the intended production host/entrypoint for approval requests and confirmations.
- Ensure the host exposes only the gateway to trusted UI code; model/provider/tool code receives none of the approval objects, handlers, token providers, or store references.
- Trace every writer and holder of `SQLiteToolApprovalStore.issue()` using branch-aware source inspection.
- Prove session revocation and policy revalidation immediately before handler dispatch.
- Establish lifecycle ownership and backup/restore consistency for all approval databases.
- Implement the unified ledger/outbox only after writer coverage and fault-injection requirements are met.
- Keep consequential specialist execution disabled until a real OS-level sandbox and target-side effect fencing are implemented and validated.

## Validation state

- Source inspection only; no local repository execution.
- Tests are authored but have not been executed locally.
- No production data was read or migrated. No approval database schema/path was changed. No consequential tool was enabled.
