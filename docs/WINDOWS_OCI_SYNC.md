# Windows + Oracle Cloud synchronization architecture

Status: design and validation foundation only. No network synchronization is enabled by this change.

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

1. Device enrollment: one-time pairing code displayed on the already authenticated hub; short TTL, single use, user approval and revocation.
2. Authentication: short-lived access tokens, rotating refresh credentials stored in OS credential storage on Windows and a protected secret store on Linux. Never store plaintext tokens in sync records.
3. Transport: HTTPS only, strict origin allow-list, request size limits, rate limits and authenticated device identity.
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
