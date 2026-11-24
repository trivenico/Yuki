# WI-6A — mTLS HTTPS audit-anchor provider client

## Goal

Replace the abstract-only anchor injection point with a concrete authenticated HTTPS client that can communicate with an independently operated checkpoint service.

## Implementation

Adds HttpsAuthorizationAuditAnchor in core/authorization_audit.py.

- Requires an HTTPS endpoint without embedded credentials, query strings, or fragments.
- Requires an SSLContext with certificate verification and hostname verification enabled. Host bootstrap can load a client certificate/key into this context for mutual TLS.
- Obtains a short-lived bearer credential through a trusted host-supplied token provider; tokens are never accepted from prompts, task payloads, or request input.
- Uses bounded network timeouts and bounded response bodies.
- Rejects redirects so authenticated checkpoint requests cannot be forwarded to another origin.
- Uses ETag compare-and-set: If-None-Match: * for explicit first-time provisioning and If-Match for updates.
- Treats HTTP 412 as a concurrency conflict and does not retry automatically.
- Requires the remote service to stamp publication time and return a committed checkpoint receipt plus ETag.
- Validates response shape, duplicate JSON keys, event ID/hash, timezone-aware timestamp, and ETag.
- Confirms the remote committed event ID/hash matches the requested immutable checkpoint.

The checkpoint publisher was adjusted to compare committed event identity (event_id + event_hash), not the client clock timestamp. The timestamp is authoritative only when returned by the remote service.

## Required remote service contract

The remote anchor service is a separate trust domain and must implement all of the following atomically:

1. Authenticate mTLS client identity and authorize a narrowly scoped publisher role.
2. GET the current checkpoint, returning HTTP 404 only when no checkpoint has ever been provisioned.
3. On initial PUT, require If-None-Match: * and atomically create exactly one checkpoint.
4. On subsequent PUT, require a matching If-Match ETag and atomically compare-and-set.
5. Reject a lower event ID, a same-ID/different-hash update, or a checkpoint that does not extend the immutable anchored history.
6. Stamp published_at using the service's trusted clock, persist the checkpoint durably, and return the committed record plus a new strong ETag.
7. Retain a separate administrative audit trail and support credential rotation/revocation.

The client cannot prove that the endpoint is independent, that the remote server is honestly enforcing these invariants, or that its storage is immutable. Those are service deployment, IAM, storage-retention, and operational controls.

## Explicit first-time provisioning

The scheduled publisher refuses an absent anchor. An operator-controlled bootstrap may call audit_log.publish_checkpoint(anchor) only after verifying that the intended local audit database is the correct initial history. The HTTPS client uses If-None-Match: * so concurrent or repeated bootstrap attempts cannot overwrite an existing checkpoint.

## Tests added

- verified TLS context required;
- GET/PUT ETag conditional semantics;
- server timestamp receipt handling;
- HTTP 412 conflict fail-closed behavior without retry;
- create-only first checkpoint precondition;
- redirect rejection.

Tests were added but not executed in this environment. The repository does not contain the remote anchor server implementation, its IAM policy, its durable storage, or its operational key-rotation service.
