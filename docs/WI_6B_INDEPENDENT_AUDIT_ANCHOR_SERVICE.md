# WI-6B — Independent audit-anchor service

## Scope

This milestone adds a server-side trust boundary for the HTTPS client in WI-6A. It is a self-hostable ASGI service backed by a dedicated SQLite database. It is not a hosted cloud service and is not automatically deployed by Yuki.

## Components

- core/audit_anchor_service.py — SQLiteAuditAnchorService and AuditAnchorASGI.
- tests/test_audit_anchor_service.py — service and ASGI regression tests.

## Server-side invariants

- Publisher identity is supplied only by a host-injected authenticate_scope callback. JSON request bodies cannot choose or override the caller identity.
- Publisher enrollment and revocation are separate administrative operations. They require a host-injected administrator authorization callback.
- Every enrollment/revocation is persisted in a separate administrative audit table with administrator identity, target publisher, operation, and server timestamp.
- Checkpoint publication uses SQLite BEGIN IMMEDIATE and a strong ETag compare-and-set.
- First publication requires If-None-Match: *; updates require the exact current If-Match ETag.
- Lower event IDs, same-ID/different-hash changes, unchanged hashes at a higher event ID, stale ETags, and unrecognized preconditions are rejected.
- The service stamps published_at using its own UTC clock and returns the committed receipt plus a new strong ETag.
- Revoked publishers cannot read or write checkpoints.
- The ASGI boundary limits request bodies, rejects duplicate JSON keys and duplicate conditional headers, returns generic errors, and sets no-store/nosniff response headers.
- SQLite database files are created with the repository's private-file path hardening.

## Trust and deployment requirements

Run this service in a separate account, host, container, or security boundary from the Yuki application and its authorization audit database. The service's SQLite database must live on a filesystem with reliable local SQLite locking; do not place it on an arbitrary network filesystem.

The ASGI app does not terminate TLS. Deploy it behind a TLS endpoint that requires and validates client certificates (or an equivalent workload identity gateway), then inject the authenticated publisher identity into the ASGI scope through trusted middleware. Do not derive identity from arbitrary X-Forwarded-* headers. Network policy must prevent direct unauthenticated access to the ASGI process.

The host must implement:
- client certificate issuance, rotation, expiry monitoring, and emergency revocation;
- a short-lived bearer token issuer/validator if the WI-6A bearer token is used in addition to mTLS;
- administrator authentication and authorization for set_publisher;
- secure backup, retention, alerting, and independent administrative access;
- service availability monitoring and a recovery plan.

The service stores publisher identities, not bearer tokens or private keys. Credential lifecycle and administrator identity proof are deliberately injected by the host rather than implemented as an insecure local password database.

## Monotonicity boundary

The service enforces increasing event IDs and compare-and-set against the previously committed checkpoint. It rejects same-ID hash changes and hash reuse at a later event ID. It cannot independently reconstruct or validate every intermediate event in Yuki's audit chain from a single head checkpoint. The trusted publisher must verify its local chain before publishing; independent evidence is the server-held prior checkpoint and its administrative trust boundary.

## Remaining work

- Deploy the service in a separate infrastructure trust domain.
- Provide real mTLS identity middleware and short-lived bearer token validation.
- Store the anchor in independently backed-up, access-controlled storage; SQLite here is a starter implementation, not a multi-region or high-availability service.
- Add external monitoring, credential rotation/revocation automation, restore drills, and disaster-recovery verification.
- Configure Yuki's production bootstrap to use the remote service endpoint and separately managed credentials.

Tests were added but not executed in this environment. The remote service is not deployed or live-tested by this PR.
