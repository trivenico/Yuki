# Phase 207 — component runtime readiness endpoint

## Goal

Expose an operator-facing readiness probe for the authenticated API runtime
without changing the existing liveness endpoint or disclosing internal
component diagnostics to unauthenticated callers.

## Implementation

- Add `GET /readyz` and `HEAD /readyz` handling at the outer
  `ConversationRuntime` ASGI boundary.
- Return HTTP 200 only when the registered component runtime reports every
  started component healthy.
- Return HTTP 503 when the registry is absent, no components are started, a
  health check fails, health evidence is incomplete, or report construction
  raises an unexpected exception.
- Unexpected probe errors are converted to a minimal `unavailable` response;
  internal exception text is never returned by the unauthenticated endpoint.
- Return a deliberately small JSON payload containing only overall readiness
  and the component-runtime status; detailed exception text and component
  diagnostics remain in the internal readiness report.
- Reuse the web application's shared response helper so readiness responses
  receive the same security headers, cache policy and HEAD semantics as other
  web responses.
- Preserve `GET /healthz` as a liveness check. Liveness means the process can
  answer; readiness means the configured component runtime is started and
  healthy.

## Operational behavior

- A deployment that does not configure a component registry will receive
  `503` from `/readyz`. This is intentional fail-closed behavior, not a
  claim that the API process itself is stopped.
- Configure and start the required component registry before routing traffic
  to this instance.
- The endpoint does not test model inference, provider credentials, external
  integrations, database restore capability, voice, or desktop automation.
  Those require separate component health checks and acceptance tests.
- The readiness response is not an authentication mechanism and must not be
  used as a substitute for network access controls.

## Tests and evidence

- Healthy, unhealthy and unconfigured registry states.
- HEAD returns the readiness status with an empty response body.
- Exact-head CI is required across supported operating systems and Python
  versions. No production deployment or full-platform readiness is implied
  by this endpoint alone.
