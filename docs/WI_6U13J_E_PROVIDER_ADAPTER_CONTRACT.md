# WI-6U13J-E — External provider adapter contract and activation gate

## Status

Provider-neutral contract only. No provider has been selected. No provider SDK, OAuth flow, credential, dispatcher, network client, webhook receiver, or mutating action is introduced by this work.

The current production posture remains **external provider execution disabled**. A provider adapter must not be registered or callable merely because a workflow contains an action name.

## Trust boundaries

The application layer is the only component allowed to construct an authorized action request. Model-generated workflow definitions may select only an action from a deployment-owned allowlist; they cannot supply a provider URL, HTTP method, headers, OAuth scope, tenant identity, credential reference, idempotency key, or authorization decision.

The adapter receives a narrow, typed request assembled from verified tenant, initiating principal, agent, task, and current task lease context; a server-registered provider and action identifier plus immutable action version; centrally authorized, schema-validated, size-bounded input; the durable operation ID and stable provider idempotency key; and a deployment-owned credential handle resolved just in time.

The adapter must not receive raw signing keys, workload credentials, delegation tokens, database connections, or unrestricted application service objects.

## Required adapter capabilities

1. **Authentication** — deployment-owned secret storage, least-privilege provider scopes, rotation/revocation, no credentials in task definitions, logs, traces, operation events, or exceptions.
2. **Request validation** — strict typed input/output schemas, bounded strings/collections/payload sizes, canonical serialization, explicit data minimization, and no arbitrary URL or header forwarding.
3. **Idempotency** — stable provider key derived from the durable operation identity; document key scope, retention window, conflict behavior, and whether repeated requests return the original result.
4. **Outcome lookup** — a provider lookup or reconciliation path that can distinguish accepted, rejected, and genuinely unknown outcomes. Document consistency delays and lookup limitations.
5. **Timeout and retry semantics** — classify failures as definitely-not-sent, definitely-rejected, accepted, or ambiguous. Never retry an ambiguous request unless the provider's documented idempotency contract makes the exact same request safe.
6. **Rate limits and backpressure** — bounded concurrency, provider-specific rate limiting, safe Retry-After handling, circuit breaking, and no unbounded retry loops.
7. **Audit and privacy** — persist only bounded provider reference, result digest, normalized error code, and necessary timestamps. Never persist full provider response bodies by default.
8. **Revocation and authorization** — perform the just-in-time Yuki dispatch gate immediately before provider I/O; provider credentials and scopes do not replace Yuki's tenant/action/delegation authorization.
9. **Network security** — fixed provider origins, TLS verification, outbound egress policy, redirect restrictions, and no model-controlled destinations.
10. **Operational readiness** — documented incident response, credential rotation, reconciliation ownership, alerting, provider outage behavior, and a kill switch that disables the action without deleting durable intents.

## Required execution sequence

1. Validate the registered action schema and resolve a deployment-owned provider credential handle.
2. Obtain the current verified workload context and create/reuse the durable intent through AuthorizedExternalActionService.
3. Claim only the matching tenant/agent/task/action/policy scope.
4. Call the just-in-time dispatch authorization gate using the current operation lease generation and expected version.
5. Resolve the provider credential just in time and make one bounded provider call using the stable provider idempotency key. Do not hold a database transaction open during network I/O.
6. On a definitive success or rejection, persist a bounded terminal result through the authorized lifecycle API.
7. On timeout, connection loss after possible transmission, process crash, or any ambiguous outcome, mark the operation unknown. Do not automatically replay it.
8. Resolve unknown only through provider lookup and the explicit authorized reconciliation path. If lookup cannot establish the outcome, keep the operation unknown and escalate to an operator.

A dispatch authorization result is a short-lived gate, not a transferable capability. The adapter must not cache it or use it for a different operation.

## Provider onboarding evidence

Before enabling one provider/action, require a provider-specific record containing provider and action name/version, endpoint/origin, and required OAuth scopes; authoritative provider documentation for idempotency, lookup, retention, and error semantics; exact request/response schemas and data classification; credential storage, rotation, revocation, and tenant isolation design; failure matrix including crash before send, after send, after acceptance, and before result persistence; fake-provider contract tests plus provider sandbox tests; rate-limit, timeout, retry, and reconciliation runbooks; security review, privacy review, operational owner, and rollback/kill-switch procedure.

A sandbox test alone is not evidence of production exactly-once behavior.

## Activation gates

The action remains disabled until all of the following are true:
- prerequisite ownership, verified-context, authorized-intent, and crash/reconciliation changes are merged and their exact-head CI is green;
- provider-specific idempotency and lookup semantics have been verified against authoritative documentation and sandbox behavior;
- tests prove no dispatch without current authorization, no cross-tenant or cross-action access, no blind replay, and correct reconciliation;
- production secrets, least-privilege scopes, outbound network policy, monitoring, and operational runbooks are in place;
- a human operator explicitly enables the action through deployment configuration.

## Current decision

No provider has been chosen. The next implementation must be scoped to one provider and one low-risk action, with its actual idempotency and lookup contract documented. Until that decision is made, the only safe progress is provider-neutral contract work and tests; do not ship a generic arbitrary-HTTP action or enable external side effects.