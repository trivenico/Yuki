# Workflow Approval Session Revocation Is Not Integrated With Authentication Logout

- **Severity:** Medium
- **Area:** Workflow approval lifecycle / session revocation
- **Status:** Confirmed by source inspection
- **Scope:** `automation/workflow_approval.py`, `automation/workflow_store.py`, `core/auth.py`

## Finding

Workflow approval is created from an authenticated principal. `workflow_principal_resolver()` resolves the supplied session token through `BuiltInAuthService.resolve_session()`, then returns the user ID, raw session token as `session_id`, and current permissions. `WorkflowApprovalService.approve()` verifies `workflow:approve` and persists the approval using the session ID.

The workflow store hashes that session ID and stores it in `approved_session_hash`. It also maintains a `revoked_approval_sessions` table. Both `claim_next()` and `is_execution_authorized()` reject approvals whose session hash appears in that table. This is a sound store-level revocation mechanism when it is invoked.

However, the built-in authentication lifecycle does not invoke it:
- `BuiltInAuthService.revoke_session()` marks the authentication session revoked only in `auth_sessions`.
- `BuiltInAuthService.disable_user()` disables the user and revokes rows in `auth_sessions`.
- Neither method calls `WorkflowRunStore.revoke_approval_session()`.
- Repository-wide source search found no call site integrating these methods.

Therefore, revoking the authentication session after workflow approval does not revoke the corresponding workflow approval. A worker can still claim and execute the approved workflow while its approval TTL remains valid (currently 600 seconds), because the workflow store's own revocation table has not been updated.

## Impact

A user who logs out, or an administrator who disables that user, may still have previously approved queued workflows executed during the remaining approval lifetime. This weakens the intended session-bound approval lifecycle and is especially relevant for shared desktop sessions or administrative account disablement.

The finding concerns approvals already persisted before logout/disablement. The principal resolver correctly rejects a session that is already revoked at the time a new approval is requested.

## Recommended remediation

1. Introduce a trusted session-revocation coordinator that updates both authentication and workflow approval state.
2. Ensure logout and account-disable flows revoke all relevant workflow approval sessions before reporting successful revocation.
3. Consider a durable transactional outbox or equivalent recovery mechanism if authentication and workflow stores cannot share one transaction; fail closed while revocation propagation is pending.
4. Add integration tests proving that logout and account disablement prevent queued, previously approved workflows from being claimed and that `is_execution_authorized()` returns false.
5. Ensure the integration handles all active sessions for a disabled user, not only the current desktop token.

## Evidence and limitations

- `core/auth.py`: `revoke_session()` updates `auth_sessions`; `disable_user()` disables the account and revokes its authentication sessions.
- `automation/workflow_approval.py`: `workflow_principal_resolver()` resolves the live session; `WorkflowApprovalService.approve()` persists the principal's session ID.
- `automation/workflow_store.py`: `approve()` hashes and stores the session ID; `revoke_approval_session()` inserts its hash into the revocation table; `claim_next()` and `is_execution_authorized()` consult that table.
- No repository call site was found connecting authentication revocation to workflow approval revocation.

Source-review finding only. No tests, CI, or runtime experiments were executed for this audit pass.
