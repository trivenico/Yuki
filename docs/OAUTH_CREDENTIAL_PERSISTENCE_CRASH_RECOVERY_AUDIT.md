# OAuth Credential Persistence Crash-Recovery Gap Audit

## Scope

Source review at PR #1346 head `131a332bb83ab1451db5609bd5f496a0e5390cf9`. Reviewed authorization completion ordering, the encrypted credential vault, connection registry, callback API compensation, and the existing partial-failure and rejected-result audits.

## Finding: process termination between credential persistence and registry registration leaves an unreconciled orphan credential

**Severity: Medium — durable credential/connection state divergence and blocked reauthorization.**

The authorization service persists the credential before the API writes the connection metadata:

1. `OAuthAuthorizationService.complete()` validates the callback, exchanges the authorization code, validates the grant, verifies identity, and calls `credential_vault.store()`.
2. It returns provider/account/scopes and credential version to the API.
3. `AuthenticatedOAuthAuthorizationAPI` validates the result and then separately calls `registry.register()`.
4. If registry registration raises, the API calls `discard_unregistered_grant()` as an in-process compensating action.

The vault and connection registry are separate SQLite stores. There is no durable intent/outbox row recording that a grant was persisted but its connection metadata is not yet committed, and no startup or periodic reconciliation mechanism is visible in the inspected flow.

### Failure sequence

1. Callback completes token exchange and stores the encrypted credential successfully.
2. The process exits, is killed, or crashes before the registry registration call commits.
3. The in-memory callback stack and its exception handler disappear; `discard_unregistered_grant()` is never invoked.
4. The vault retains the credential while the connection registry has no corresponding row.
5. Owner-facing connection listing reads only the registry, so the connection is not discoverable through that endpoint.
6. A later authorization for the same owner/provider/account can be rejected by the vault's duplicate-key check (`credential_already_exists`), while the orphan credential remains outside the ordinary connection listing/disconnect workflow.

The same unrecoverable window exists if the process dies after registry registration fails but before the compensation operation commits.

## Evidence

- `core/oauth_authorization_service.py`: `complete()` calls `self._vault.store(...)` before returning the credential version and metadata.
- `api/oauth_authorization.py`: callback calls `service.complete()`, then validates its result, then calls `self.registry.register()`. The compensation hook exists only in the exception handler around registry registration.
- `core/oauth_credential_vault.py`: credential rows are uniquely keyed by owner/provider/account; `store()` rejects an existing row with `credential_already_exists`.
- `core/oauth_connection_registry.py`: registry rows are stored independently in a separate SQLite database and the owner listing reads only this registry.
- `api/oauth_connections.py`: ordinary disconnect first looks up a registry record and returns `connection_not_found` if no record exists, so this orphan is not discoverable through the normal connection-delete route.
- No durable callback-completion journal, reconciliation queue, or vault-to-registry startup repair was found in the inspected paths.

## Distinction from existing audits

- `docs/OAUTH_CONNECTION_REGISTRY_PARTIAL_FAILURE_AUDIT.md` covers registry deletion failure after credential deletion and stale registry metadata; it also discusses registry listing truncation.
- `docs/OAUTH_AUTHORIZATION_REJECTED_RESULT_COMPENSATION_AUDIT.md` covers invalid completion metadata rejected after service persistence.
- This report covers abrupt process termination after credential persistence and before registry registration or completion of compensation. It is not dependent on a registry exception or malformed service result.

## Impact and boundaries

- Credential and connection metadata can permanently diverge after an ordinary process crash.
- The orphan credential can block reauthorization due to the vault's unique key constraint.
- The connection listing and standard disconnect endpoint cannot discover the orphan when no registry row exists.
- The credential remains encrypted at rest; this is a lifecycle consistency and recoverability issue, not a plaintext exposure.
- Whether a deployment has an external administrative vault cleanup process is host-specific and was not established by this source review.

## Recommended remediation

1. Add a durable authorization-completion journal with states such as `grant_persisted`, `connection_registered`, `compensation_pending`, and `recovery_required`.
2. Persist a unique grant/lifecycle identifier and credential version in that journal. Avoid relying on version 1 alone across delete/recreate lifetimes.
3. Make callback completion idempotently advance the journal after registry registration; use version-fenced compensation for definitive registration failure.
4. Run a bounded startup/periodic reconciler that detects journal entries left incomplete, verifies the exact credential generation, and either completes registry registration or safely compensates according to an explicit recovery policy.
5. Ensure the reconciler also detects vault rows with no registry row, while avoiding deletion of credentials whose authorization completion is still legitimately in flight.
6. Provide an administrative recovery path for legacy orphan rows created before journaling is deployed.
7. Add crash-point tests by injecting process termination after vault store, after registry failure, and after registry commit but before journal advancement. Verify retries are idempotent and never delete a newer credential generation.

Because the stores are separate SQLite databases, do not claim a single atomic transaction across them. A durable journal/outbox and idempotent reconciliation are required to bridge the boundary.

## Review limitations

No runtime code was changed and no tests were executed during this source review. The failure window is derived from source ordering and independent persistence boundaries; no process-kill reproduction was run.
