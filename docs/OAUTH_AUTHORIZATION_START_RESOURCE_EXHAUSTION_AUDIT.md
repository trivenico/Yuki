# OAuth Authorization Start Abuse and Resource-Exhaustion Audit

## Scope

Source review at PR #1346 head `a7b607ad5cb401ee8706ee47fda6adb53fc26858`. Reviewed the authenticated OAuth authorization API, durable SQLite transaction store, authorization service, prepared-provider bootstrap, and runtime composition.

This is a bounded source-level finding. It does not establish whether an external reverse proxy, API gateway, or deployment-specific middleware applies rate limits.

## Finding: authorization starts have no application-level admission control or per-owner pending-transaction bound

**Severity: Medium (availability / resource exhaustion; deployment dependent).**

The authenticated endpoint `POST /v1/integrations/oauth/providers/{provider}/authorize` authenticates the session and resolves a trusted provider, then calls `OAuthAuthorizationService.begin()` for every request. There is no visible per-owner, per-session, per-provider, or global start quota in this API path.

Each successful start:
1. Generates fresh PKCE state/verifier (and optional OIDC nonce).
2. Encrypts verifier/nonce through the injected cipher.
3. Opens the SQLite transaction store and performs a durable `BEGIN IMMEDIATE` insert.
4. Persists a new row with a ten-minute lifetime.

The transaction store bounds each row's TTL and individual field sizes, but it does not bound the number of outstanding rows for an owner/session/provider. Repeated authenticated requests can therefore generate unbounded pending transaction rows and repeated encryption/SQLite write work. The API's request-body size bound does not constrain this empty-body operation.

Expired-row cleanup exists as `purge_expired()`, but the reviewed provider bootstrap and API composition do not invoke or schedule it. Even if a host schedules cleanup, it does not prevent a burst of pending starts within the TTL window.

This creates a resource-exhaustion path for a user or attacker controlling a valid session, potentially increasing SQLite write contention and database growth and degrading authorization callbacks that share the transaction database. The practical impact depends on session issuance controls, deployment-level rate limits, database capacity, and cleanup scheduling.

## Evidence

- `api/oauth_authorization.py`: the authorize branch authenticates, resolves the provider, and invokes `service.begin` without an application-level quota/admission check.
- `core/oauth_authorization_service.py`: each `begin` creates a new PKCE request and persists a transaction with `ttl_seconds=600`.
- `core/oauth_transactions.py`: `create` inserts one row per unique state; no pending-count limit or owner/session/provider quota is enforced. `purge_expired` is a separate explicit method.
- `core/mcp_oauth_provider_bootstrap.py` and `api/runtime.py`: reviewed composition wires the shared store and authorization API but does not schedule transaction cleanup.
- Existing transaction tests cover TTL bounds, persistence, atomic consumption, replay prevention, and explicit purge behavior; they do not cover per-owner admission limits, concurrent start floods, or sustained pending-row growth.

## Recommended remediation

1. Add an atomic admission-control mechanism keyed by authenticated owner, session, and provider, with a global safety ceiling. Enforce it inside the durable transaction store (or a shared limiter) so multiple workers cannot bypass a process-local counter.
2. Bound outstanding unconsumed transactions per owner/session/provider. Expired or consumed rows should not count against the pending quota.
3. Return a stable `429` response with a bounded `Retry-After` when admission is denied; avoid exposing internal quotas or transaction state.
4. Schedule bounded, observable `purge_expired` maintenance and define retention appropriate for incident investigation. Cleanup must not be the only protection against bursts.
5. Consider idempotent reuse only if it is safely bound to the initiating session and does not weaken state unpredictability or one-time callback semantics; otherwise reject excess starts rather than silently replacing valid pending state.
6. Add tests for concurrent starts across independent store instances, quota enforcement under SQLite contention, expiry/consumption freeing quota, global limits, and cleanup failure.

## Boundaries and non-findings

- Authentication is required before the authorization start reaches the service.
- Provider IDs and endpoint configuration are trusted host-resolved; this is not a user-controlled arbitrary-URL issue.
- Per-row TTL and field bounds are present.
- State consumption is atomic and single-use; this finding does not indicate a callback replay vulnerability.
- No claim is made that deployment-level gateway limits are absent. Verify them in the actual production ingress configuration.
- No runtime code was changed and no tests were executed as part of this source review.
