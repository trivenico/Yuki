# OAuth Connection Registry Partial-Failure and Enumeration Audit

## Scope

Source review at PR #1346 head `a77ab058a1865ececdfa3b768a17f3197f312c42`. Reviewed the SQLite connection registry, authenticated connection API, disconnect service, authorization completion registration path, and registry tests. Existing disconnect/reconnect generation race is documented separately and is not repeated here.

## Finding 1: registry deletion failure leaves a stale connection after credential deletion

**Severity: Medium (connection-state integrity and recovery).**

The authenticated DELETE flow performs two independent durable operations:

1. `OAuthDisconnectService.disconnect()` deletes the credential from the credential vault.
2. Only after that succeeds, `AuthenticatedOAuthConnectionAPI` calls `registry.remove()`.

If the registry database is unavailable, locked beyond its timeout, full, or otherwise fails during step 2, the API's outer exception handler returns `400 invalid_request`. The vault deletion has already committed and cannot be rolled back by this handler. The registry row remains and the API has no durable repair marker, retry queue, or reconciliation mechanism.

The owner-facing GET endpoint reads only the registry. It can therefore continue reporting `status: connected` for a credential that no longer exists. A subsequent DELETE may fail in the disconnect service's credential lookup path, leaving the stale registry row indefinitely. This differs from the already documented concurrent reconnect race: this partial state arises from an ordinary storage failure between two sequential writes, without concurrency.

Authorization completion already has a compensating path when registry registration fails. The disconnect path has no analogous durable completion/reconciliation mechanism after vault deletion.

## Finding 2: owner listing silently truncates at 100 records

**Severity: Low (functional correctness / manageability).**

`SQLiteOAuthConnectionRegistry.list_for_owner()` executes an ordered query with `LIMIT 100` and exposes no cursor or total count. The registry's `register()` method does not cap row count. If an owner has more than 100 connections, the API silently omits older records, while DELETE still supports direct provider/account paths. Users cannot discover all their connections through the list endpoint, and stale entries beyond the first page can remain difficult to identify or remove.

## Evidence

- `api/oauth_connections.py`: after `service.disconnect()` returns `disconnected: true`, the API calls `registry.remove()`; any exception falls through to the broad handler and returns `400 invalid_request`.
- `core/oauth_disconnect_service.py`: vault deletion is performed and committed inside `disconnect()` before the API attempts registry removal.
- `core/oauth_connection_registry.py`: `remove()` is an independent SQLite transaction; there is no cross-database transaction or reconciliation record. `list_for_owner()` hard-caps results at 100 with no pagination metadata.
- `core/oauth_authorization_service.py` and `api/oauth_authorization.py`: authorization completion has a version-fenced compensation hook for registry registration failure, highlighting the absence of a comparable disconnect repair path.
- `tests/test_oauth_connection_registry.py`: tests cover basic register/list/get/remove isolation and upsert behavior, but not deletion failure after vault deletion or more than 100 records.

## Recommended remediation

1. Treat vault deletion as the authoritative security action: once the credential is deleted, report disconnect success or a distinct `cleanup_pending` state rather than returning a generic bad-request response.
2. Persist a durable reconciliation/outbox record before or as part of the vault deletion lifecycle, and retry registry cleanup idempotently. Since vault and registry are separate SQLite stores, do not imply atomicity across them.
3. Add an operational reconciler that compares registry entries with vault credentials and repairs stale metadata, with bounded batches and metrics. Define policy for credentials missing registry entries as well.
4. Make list pagination explicit with a stable cursor, bounded page size, and optional total/has_more metadata; avoid silent truncation.
5. Add deterministic failure-injection tests: force registry removal to fail after successful vault deletion, assert the API response accurately reflects partial completion, and verify reconciliation eventually removes the stale row. Add pagination tests with more than 100 records.

## Boundaries and non-findings

- The existing disconnect/reconnect generation race is covered by `docs/OAUTH_DISCONNECT_RECONNECT_REGISTRY_RACE_AUDIT.md`; this report does not duplicate it.
- The authorization registration path already attempts version-fenced credential compensation when registry registration fails.
- Registry rows contain non-secret metadata; the issue is consistency and discoverability, not bearer-token disclosure.
- No runtime code was changed and no tests were executed as part of this source review.
