# OAuth disconnect/reconnect registry race

## Finding

**Severity: Medium — a completed disconnect can erase a concurrent reconnect's registry entry**

The authenticated disconnect API deletes the credential and then unconditionally removes the owner/provider/account registry row. These are separate operations and there is no connection generation or compare-and-delete condition on the registry removal.

## Source evidence

- `api/oauth_connections.py`, `AuthenticatedOAuthConnectionAPI.__call__`:
  1. Reads the current registry record with `registry.get(...)`.
  2. Calls the provider disconnect service.
  3. After the service reports local credential deletion, calls `registry.remove(owner_id, provider_id, account_id)`.
- `core/oauth_disconnect_service.py`, `OAuthDisconnectService.disconnect()`: retrieves the current credential, optionally attempts remote revocation, and then calls `vault.disconnect(...)`.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.store()`: rejects duplicate credentials, but permits a new credential to be stored after the previous row has been deleted.
- `core/oauth_connection_registry.py`, `SQLiteOAuthConnectionRegistry.register()`: upserts by owner/provider/account. A concurrent reconnect can therefore create/update the registry row after the old credential has been deleted.
- `SQLiteOAuthConnectionRegistry.remove()` deletes solely by owner/provider/account. It has no expected connection version, timestamp, or generation predicate.

## Interleaving

1. Disconnect request D reads the old registry record.
2. D revokes the old remote token (or skips remote revocation), then deletes the old vault credential.
3. Reconnect request R completes OAuth and stores a new credential for the same owner/provider/account. The vault accepts it because the old row is gone.
4. R registers the new connection row.
5. D resumes and unconditionally removes that registry row.
6. Final state: the new credential exists, but the new registry entry is absent.

This race does not require an attacker to forge provider identity. It is a concurrency flaw in the normal trusted reconnect/disconnect lifecycle.

## Impact

The registry and vault can diverge. The newly authorized credential may become undiscoverable to registry-driven connection management and MCP runtime composition. Subsequent authorization attempts may fail with `credential_already_exists`, while the owner-facing connection list reports the account as disconnected.

The remote revocation operation is performed against the credential observed by the disconnect service before local deletion. The primary confirmed issue is the later unconditional registry removal deleting a newer registration.

## Recommended remediation

- Give each registry connection a monotonically increasing generation or opaque connection ID.
- Have disconnect capture the generation it observed and remove only that exact generation using a conditional SQL DELETE.
- Have authorization registration create a new generation atomically with its registry upsert.
- Prefer a shared lifecycle coordinator or durable state machine for vault and registry transitions, because they currently live in separate SQLite stores and cannot be committed atomically.
- Ensure a stale disconnect cannot delete a newer credential. Extend vault disconnect to accept an expected credential version and pass the version read before remote revocation; the current disconnect service uses an unconditional vault deletion. This is a separate related race if a refresh or credential replacement can occur during the remote revocation request.
- Add deterministic barrier-based tests that pause disconnect after vault deletion, complete a reconnect, then resume disconnect; assert the new registry generation remains present.
- Add a second concurrency test for credential version changes between disconnect's initial read and its final vault deletion.

## Scope and confidence

The registry-removal race follows directly from the current source ordering and the vault/registry write semantics. It is a source-confirmed race, not an observed production incident. The credential-version race is a related hardening issue: `OAuthDisconnectService` reads a credential, performs a potentially blocking remote revocation, then calls an unconditional vault delete rather than deleting the version it originally observed.

No runtime tests were executed during this source review.
