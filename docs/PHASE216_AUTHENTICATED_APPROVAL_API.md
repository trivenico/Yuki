# Phase 216 — Authenticated approval inbox HTTP boundary

## Implemented

- Restored the authenticated capability approval service contract from merged approval work (#619–#620) onto the claim-bound branch, where that service was missing from the branch ancestry.
- Added authenticated ASGI routes:
  - `POST /v1/approvals/requests` — request approval for a validated exact `PolicyRequest`; the service binds its principal to the authenticated session.
  - `GET /v1/approvals/inbox` — list redacted pending requests; requires `workflow:approve`.
  - `POST /v1/approvals/requests/{request_id}/approve` — approve a pending request; requires `workflow:approve`.
  - `POST /v1/approvals/requests/{request_id}/reject` — atomically reject a pending request; requires `workflow:approve`.
  - `GET /v1/approvals/audit` — read redacted audit metadata; requires `workflow:read`.
- Added atomic gateway rejection with authenticated approver validation and an audit event.
- Wired the optional approval API into the ASGI runtime. The service must use the exact same authentication-service instance as the runtime.
- Enforced bounded JSON bodies, strict field allowlists, validated request identifiers, bearer-session authentication, no-store response headers and generic error responses.
- Approval tokens are never serialized in HTTP responses. They are delivered only through the service's injected protected credential callback; callback failures are sanitized and cause best-effort gateway revocation.
- Added API and runtime composition tests for owner binding, privilege checks, approval/rejection, redaction, delivery failure and route delegation.

## Operational and security boundaries

- The API is optional until an authenticated approval service is explicitly supplied to runtime composition.
- Approval delivery is not automatically wired to a live worker. A production composition must supply a protected callback that can obtain the exact active worker claim lease secret and invoke the worker-pinned broker. The HTTP layer intentionally cannot accept or return that lease secret.
- The branch's worker broker requires a lease token for delivery and independently validates the exact claim generation. A callback that cannot prove the active claim must fail closed.
- Inbox listing is available to principals with `workflow:approve`; in the current single-owner product configuration, bootstrap the owner as the administrator and do not expose account creation or role escalation to untrusted users.
- This adds API capability, not a finished approval UI or an authenticated deployment configuration.

## Remaining production requirements

- Implement and test the production credential-delivery adapter that bridges the authenticated approval service to the exact active worker's lease manager and broker.
- Build an owner-facing approval inbox UI with a trusted, human-readable operation preview (capability adapters must supply this; do not trust model-generated summaries), consequences and clear risk. Current inbox metadata intentionally does not claim to explain the actual arguments.
- Add per-route rate limiting, deployment TLS/session-cookie or bearer-token policy review, CSRF review if cookie auth is introduced, and structured security telemetry.
- Add cross-process delivery/recovery race tests, KMS-backed AEAD and key rotation, provider-side commit fencing, backup/restore validation and release certification.

This phase is an authenticated HTTP boundary, not a complete production approval workflow. Approval actions remain unavailable if protected credential delivery is not configured.
