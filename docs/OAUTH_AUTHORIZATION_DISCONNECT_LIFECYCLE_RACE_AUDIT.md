# OAuth Authorization Completion vs Disconnect Race Audit

## Scope

Source review at PR #1346 head `e3af3390cd366c4b9f105ce1eb84cefc8d94afb9`. Reviewed authenticated authorization and connection APIs, authorization service, disconnect service, credential vault, connection registry, transaction store, and related tests.

## Finding: an in-flight authorization callback can recreate a connection after disconnect

**Severity: Medium — a completed user disconnect can be undone by an authorization flow already in progress.**

The callback and disconnect workflows have no shared account-lifecycle lock, cancellation marker, or generation fence spanning credential persistence and connection registration.

### Relevant execution paths

Authorization callback:

1. `AuthenticatedOAuthAuthorizationAPI.__call__()` resolves the authenticated session and selects the provider authorization service.
2. It invokes `service.complete()` in a worker thread.
3. `OAuthAuthorizationService.complete()` atomically consumes the pending state, exchanges the authorization code, validates the grant and identity, and calls `credential_vault.store()`.
4. After `complete()` returns, the API separately calls `registry.register()`.

Disconnect:

1. `AuthenticatedOAuthConnectionAPI.__call__()` reads the current connection record.
2. It resolves the provider disconnect service and invokes `service.disconnect()`.
3. `OAuthDisconnectService.disconnect()` reads the current credential, optionally performs remote revocation, then calls `vault.disconnect()` without an expected credential version.
4. The API separately removes the connection registry row.

There is no shared transaction or durable account lifecycle state coordinating these sequences.

### Concrete race

For an existing connected account that has initiated a fresh authorization flow:

1. Callback consumes its pending OAuth transaction and starts the remote token exchange. It has not yet stored the new credential.
2. Owner invokes disconnect. The disconnect service reads and revokes the existing credential, deletes it, and the API removes the registry row.
3. The callback's token exchange and identity verification finish.
4. The callback stores a new credential under the same owner/provider/account key. The vault permits this because disconnect deleted the previous row; the new row starts at version 1.
5. The callback API registers the connection again.

The disconnect request can return success before the callback recreates the connection. The callback transaction was already consumed, but transaction consumption does not make it aware of a later disconnect.

The inverse interleaving is also problematic: if the callback stores first, disconnect may delete that new row before the callback registers its metadata, leaving the callback to register a connection record with no credential. The API's registry-registration compensation only runs when registration itself raises; it does not re-check whether the credential still exists.

### Evidence

- `api/oauth_authorization.py`: invokes `service.complete()`, then independently registers its result in the connection registry.
- `core/oauth_authorization_service.py`: `complete()` performs token exchange and identity verification before storing the credential; no account lifecycle generation is checked before or after network operations.
- `api/oauth_connections.py`: disconnect reads a registry record, calls the disconnect service, then independently removes the registry record.
- `core/oauth_disconnect_service.py`: reads and revokes the credential, then deletes it without a generation or pending-authorization fence.
- `core/oauth_credential_vault.py`: `store()` inserts a new credential row when the key is absent; `disconnect()` deletes the current row. A newly inserted row starts at version 1, so credential version alone is not a durable lifecycle fence across deletion/recreation.
- `core/oauth_transactions.py`: transaction consumption is one-time and owner/session-bound, but there is no account-level cancellation operation connected to disconnect.
- Existing API/service tests cover successful callback, disconnect, and registry-failure compensation, but not the interleaving between a callback paused during exchange and a concurrent disconnect.

## Impact and boundaries

A user-initiated disconnect is not a durable barrier against OAuth callbacks already in flight. A pending authorization can persist fresh credentials and re-register the account after disconnect has completed.

A different interleaving can leave registry metadata registered after its credential has been deleted. The exact observed outcome depends on scheduling and provider exchange duration.

This finding is distinct from the previously documented refresh/disconnect generation race: this race is between a new authorization grant and disconnect, and the authorization path inserts a new credential after deletion rather than rotating an existing credential.

## Recommended remediation

1. Add a durable per-owner/provider/account lifecycle generation or tombstone that survives credential deletion and recreation.
2. Bind each pending OAuth transaction to the lifecycle generation captured at authorization start.
3. On disconnect, atomically advance/retire the lifecycle generation and invalidate pending transactions for that account.
4. Before credential persistence, require the transaction's captured generation to still be current. Make the check and credential insert atomic with lifecycle state in the same durable store/transaction.
5. Ensure callback registration is fenced by the same generation. Do not allow a callback from an old generation to recreate registry metadata after disconnect.
6. Reconcile credential and registry state for partial failures. A registry row must not be registered unless the corresponding credential generation still exists.
7. Add deterministic concurrency tests using barriers: pause callback after transaction consumption/before vault store, complete disconnect, resume callback, and assert no credential or registry row is recreated. Add the inverse interleaving test to ensure no metadata-only connection is registered.

A process-local mutex alone is insufficient if the application can run multiple workers or restart. Use a durable compare-and-swap/transactional lifecycle fence.

## Review limitations

No runtime code was changed and no tests were executed during this source review. The race is derived from the inspected call ordering and storage semantics; no concurrent execution was run to reproduce it.
