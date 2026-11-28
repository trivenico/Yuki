# OAuth Callback Processing Resource-Exhaustion Audit

## Scope

Source review at PR #1346 head `f9b53f0adb891da64ba3ab20fb82f1fd415d44f5`. Reviewed the authenticated callback API, authorization service, transaction store, pinned OAuth transport, and callback tests.

## Finding: callback work has no explicit concurrency or execution-budget boundary

**Severity: Medium (availability; exposure depends on ingress and provider configuration).**

A valid callback consumes its transaction atomically before performing the remaining synchronous work. After consumption, `OAuthAuthorizationService.complete()` performs the token endpoint exchange, token-response validation, optional OIDC verification, provider identity verification, and credential persistence.

The ASGI API invokes the complete operation using `asyncio.to_thread`, but the callback route has no application-level semaphore, per-owner/provider in-flight limit, or bounded admission queue. A burst of distinct, valid outstanding transactions can therefore enqueue blocking callback work in the process's default executor. The executor limits active worker threads, but its submission queue is not bounded by this API. Under sufficient concurrent callbacks, queued work can increase latency and compete with unrelated `asyncio.to_thread` operations elsewhere in the process.

The pinned transport configures a 1–30 second socket timeout (10 seconds by default), which bounds individual socket operations, not the entire callback lifecycle. DNS resolution is performed synchronously by the injected resolver before connection creation and is not governed by that socket timeout. Optional OIDC and provider identity verifiers are injected synchronous components and have no timeout contract enforced by the authorization service. A slow resolver or verifier can therefore occupy callback worker threads longer than the configured HTTP socket timeout.

Atomic transaction consumption is an important mitigation: one state cannot be used by multiple concurrent callbacks, and a callback consumes state before external exchange. However, it does not bound concurrency across many separately issued states. The preceding authorization-start audit documents the absence of pending-transaction admission limits; that issue can supply the distinct valid states needed to amplify this callback-side workload.

## Evidence

- `api/oauth_authorization.py`: callback invokes `service.complete` through `asyncio.to_thread` without a callback-specific concurrency limiter or bounded admission mechanism.
- `core/oauth_authorization_service.py`: after atomic consume, callback processing performs synchronous token exchange, response validation, optional OIDC verification, identity verification, and vault persistence.
- `core/oauth_transactions.py`: `consume` uses `BEGIN IMMEDIATE` and marks a state consumed before decrypting and returning the transaction; this prevents replay but does not cap distinct in-flight transactions.
- `core/oauth_http_transport.py`: socket timeout is configured on the connection, while DNS resolution is synchronous and outside that socket timeout. Injected identity/OIDC verifiers have no enforced execution deadline at this layer.
- `tests/test_oauth_authorization_api.py` and `tests/test_oauth_authorization_service.py`: tests cover normal callback completion, validation, owner/session binding, and failure sanitization, but not callback concurrency ceilings, executor queue pressure, slow DNS, or slow verifier behavior.

## Recommended remediation

1. Add a bounded callback admission mechanism before submitting synchronous work. Use a process-wide limit for local deployments and a shared/distributed limit if multiple workers must share a provider quota.
2. Apply per-owner and per-provider in-flight limits to prevent one authenticated principal from consuming all callback capacity.
3. Reject excess work promptly with a stable `429` or `503` response and bounded `Retry-After`; do not enqueue unbounded executor work.
4. Enforce an overall callback deadline in addition to socket-operation timeouts. Use a resolver with an explicit deadline and require injected OIDC/identity verifiers to provide bounded execution behavior.
5. Keep the existing consume-before-exchange rule. Do not automatically replay a consumed authorization code after timeout or ambiguous exchange outcome; expose a safe restart/recovery path.
6. Add tests with barriers and controlled slow resolver/verifier fakes to prove concurrency limits, prompt overload rejection, no duplicate exchange, and no starvation of unrelated executor work.

## Boundaries and non-findings

- The review does not establish that a production reverse proxy, ASGI server, or deployment-level concurrency limiter is absent.
- Callback request bodies are capped at 10,000 bytes, and callback URLs are capped at 8,192 characters in the service.
- State consumption is atomic and one-time; no callback replay defect was identified.
- The finding is about bounded resource admission and blocking-work duration, not an assertion that arbitrary unauthenticated requests can directly invoke provider token exchange.
- No runtime code was changed and no tests were executed as part of this source review.
