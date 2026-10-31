# Windows + Oracle Cloud synchronization architecture

Status: backend implementation foundation. The WSGI API and credential primitives exist on the feature branch; no production deployment or Windows client synchronization is enabled.

## Target topology

- Windows laptop: Yuki Desktop and CLI; local model runtimes and private workspace remain local by default.
- Oracle Cloud Linux: user-controlled Yuki Sync Hub, API, durable sync event store, and optional cloud inference.
- Both clients connect outbound to the hub over HTTPS. The hub never needs inbound access to the Windows laptop.
- Both interfaces use the same Yuki identity, device registry, sync protocol and permission model.

## What synchronizes

Allow-listed, user-visible records:
- Conversation metadata and messages explicitly marked for sync.
- Task records and task status.
- Skill metadata and installation references (not arbitrary executable plugin source).
- User and agent preferences that are not secrets.

Not synchronized by default:
- API keys, passwords, OAuth tokens, session cookies, private keys.
- Local model weights, caches, embeddings or raw model files.
- Arbitrary workspace files, source repositories, browser profiles or local databases.
- Approval grants. Approvals are device/session scoped and must be renewed on the other device.

## Protocol foundation

`core/sync_contract.py` defines a bounded JSON event envelope with:
- opaque event, device and object identifiers;
- explicit allow-listed collection names;
- monotonically increasing per-object revision supplied by the client;
- timezone-aware timestamps;
- upsert/delete operations;
- 256 KB event limit, bounded nesting and item counts;
- recursive rejection of credential-like field names.

This is validation only. It is not a transport, identity provider, encryption implementation, or conflict resolver.

## Required service design

1. Device enrollment: one-time high-entropy pairing code with a short TTL and single-use consumption is implemented in `core/sync_auth.py`; the administrative code-creation workflow and user-facing pairing UI remain to be built.
2. Authentication: short-lived access tokens and rotating refresh credentials are implemented as hashed SQLite records; Windows Credential Manager and protected Linux secret storage are not yet wired. Refresh-token replay revokes the device.
3. Transport: `core/sync_api.py` provides authenticated push/pull/ack routes plus pairing and refresh routes, with request-size bounds. TLS termination, rate limits, trusted proxy configuration and a production WSGI server are still required.
4. Storage: server-side SQLite for a single-user starter deployment, with append-only event log, idempotency key on event_id, per-device cursor and transactional acknowledgements. Keep DB on local Linux disk; never expose SQLite over the network.
5. Conflict handling: server sequence is authoritative; per-object revisions detect stale writes. Concurrent edits return a conflict to clients instead of silently overwriting. Tombstones are retained until all active devices acknowledge past their sequence or expire under a documented retention policy.
6. Security: per-user/device authorization, device revocation, audit events, backup/restore, encrypted disks and TLS. Add end-to-end encryption for selected private content before claiming the sync hub cannot read it.
7. Operations: health endpoint, migrations, backup verification, retention controls, observability without logging message bodies or credentials.

## Deployment on Oracle Cloud

Use one small Linux VM for the first personal deployment. Run Yuki Sync Hub behind Caddy or Nginx with TLS; bind the application service to loopback or a private interface. Open only SSH (restricted to the user's admin IP) and HTTPS (443) in OCI networking. Do not expose SQLite, Redis, model ports, or development servers publicly. Keep inference optional; the hub and sync API should work without a GPU.

The VM's public address or DNS name is configured on Windows as the sync endpoint. Use an OCI firewall/security list and host firewall together. Back up the database and encryption keys separately; test restore before trusting it.

## Rollout order

- Phase A: sync event contract and validation tests (this change).
- Phase B: Linux sync API, device pairing/revocation, persistent event log and cursors.
- Phase C: Windows CLI/desktop sync client, local outbox, retry/backoff, conflict UI.
- Phase D: selective conversation/skills/tasks/preferences sync and end-to-end tests.
- Phase E: encrypted selected payloads, backup/restore, monitoring and staged release.

## Release gate

Do not enable sync by default until authentication, device revocation, replay/idempotency, conflict tests, Windows/Linux clean-install tests, TLS deployment checks and backup restore tests pass. Until then, Yuki remains usable independently on each device and local data stays local.


# Yuki Sync Hub event store

The durable local event store is implemented in `core/sync_store.py` and is used by the WSGI API. The API must still be deployed behind a production-grade TLS and authentication boundary.

## Store contract

`core/sync_store.py` implements:
- SQLite WAL-backed append-only event history.
- Device registration and revocation, scoped to a user.
- Event idempotency using a unique event identifier; retries must have byte-equivalent normalized event content.
- Per-object monotonically increasing revisions; stale writes and revision gaps return conflicts.
- Per-device monotonic acknowledgement cursors.
- User-scoped event pulls, bounded to 500 events per page.
- Transactional event insertion and head-revision updates.

## Trust boundary

The future API must authenticate a request before calling the store and pass the authenticated device ID as `authenticated_device_id`. Never trust the device ID in the submitted JSON as the caller identity. The store verifies the event device ID matches that authenticated identity and verifies that the device is active.

Device registration/revocation functions are administrative operations and must not be exposed to an unauthenticated route. Device pairing and credential issuance are intentionally not implemented here.

## Data and deployment

Use a private local Linux filesystem path, e.g. `/var/lib/yuki/sync/sync.sqlite3`, owned by the Yuki service account. Do not expose the database file, SQLite port, or filesystem directory over the network. Use HTTPS at the future API reverse proxy. Keep the event database in backup scope and test restore.

## Tests

Run:
`python -m unittest tests.test_sync_contract tests.test_sync_store -v`


## HTTP API foundation

`core/sync_api.py` adds a framework-neutral WSGI application with:
- unauthenticated, read-only `GET /health`;
- authenticated `POST /v1/events`, `GET /v1/events?after=N&limit=N`, and `POST /v1/ack`;
- a 256 KB request-body limit, bounded pull pages, JSON-only input, no-store responses, and generic internal errors;
- injected authentication: the API passes only the authenticated device ID to the event store, which independently checks enrollment and revocation.

When constructed with `SyncCredentialStore`, the API validates access tokens and exposes one-time pairing and refresh endpoints. Pairing-code creation remains an administrative/local operation. Production deployment must terminate TLS at a trusted reverse proxy, strip untrusted forwarded identity headers, rate-limit requests, and bind the WSGI server to loopback/private networking. Do not run this WSGI application as a public internet service. OS credential storage and production token lifecycle controls remain outstanding.


## Current API routes (feature branch)

- `POST /v1/pair`: consume a one-time pairing code and return access/refresh credentials.
- `POST /v1/refresh`: rotate credentials; replaying a previously rotated refresh token revokes the device.
- `POST /v1/events`: authenticated event append.
- `GET /v1/events?after=N&limit=N`: authenticated paginated pull.
- `POST /v1/ack`: authenticated monotonic acknowledgement.
- `GET /health`: read-only liveness response.

Pairing codes are created through the local administrative credential-store API; this branch does not yet expose a remote pairing-code minting endpoint. Treat access/refresh responses as secrets and never log them. The API still needs rate limiting, TLS deployment, and production server configuration.
