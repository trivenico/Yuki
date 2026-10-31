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
2. Authentication: short-lived access tokens and rotating refresh credentials are implemented as hashed SQLite records; The optional client adapter accepts native Windows Vault, macOS Keychain, and Linux Secret Service/KWallet backends only; it rejects unknown/insecure fallback backends. The Windows backend still requires validation on the target laptop. Refresh-token replay revokes the device.
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


## Cross-platform client foundation

core/sync_client.py now provides a shared CLI/desktop client layer:
- SyncOutbox: durable SQLite outbox, idempotent enqueue, bounded batches, exponential retry and conflict quarantine.
- SyncHTTPTransport: HTTPS-only API client using the platform TLS trust store, request timeouts and bounded response reads.
- KeyringCredentialVault: optional OS credential-store adapter. On Windows, install the optional dependency with `python -m pip install -r requirements-sync.txt`; verify the selected keyring backend is Windows Credential Manager before storing production credentials.
- SyncAgent: one bounded sync cycle that pushes queued events, refreshes expired credentials, validates pulled events, applies them through an injected idempotent callback, then acknowledges and advances the local cursor.

The same SyncAgent is intended to be composed by both CLI and desktop; neither interface should implement its own protocol. The application still needs to wire real task/conversation/skill persistence into the apply_remote callback and enqueue local domain changes. No background worker is automatically started yet. The first client integration should use explicit user-triggered sync and diagnostics, then add a cancellable background worker after Windows/Linux integration tests.

### Client usage outline

```python
from core.sync_client import KeyringCredentialVault, SyncHTTPTransport, SyncOutbox, SyncAgent

vault = KeyringCredentialVault()
transport = SyncHTTPTransport("https://sync.example.com")
outbox = SyncOutbox("~/.yuki/sync/outbox.sqlite3")
agent = SyncAgent(transport, outbox, vault, apply_remote=your_idempotent_apply_function)
result = agent.sync_once()
```

Expand the outbox path with Path(...).expanduser() before constructing it. Pairing is done through transport.pair(code); the returned credentials must be saved with vault.save(device_id, access_token, refresh_token). Never print or log returned token values.

## CLI integration (feature branch)

The terminal frontend now uses the shared application-facing `core/sync_runtime.py` composition:
- `/sync status`: reports the configured HTTPS hub, paired device, outbox counts and download cursor.
- `/sync pair`: prompts for a one-time pairing code without echoing it and saves returned credentials through the optional OS keyring adapter.
- `/sync run`: intentionally reports that data synchronization is disabled until real domain adapters are wired. It does not upload or acknowledge application data.

Configure the hub URL in the process environment as `YUKI_SYNC_URL=https://your-hostname`. Install the optional credential-store dependency with `python -m pip install -r requirements-sync.txt`. The client now fails closed unless the active backend is a native Windows Vault, macOS Keychain, or Linux Secret Service/KWallet backend; insecure fallback backends are rejected. The current branch does not yet provide a user-facing pairing-code minting command on the Linux hub; codes are created by the local administrative `SyncCredentialStore.create_pairing_code` API and must be delivered securely.

The desktop app should import and use `SyncRuntime` rather than implementing its own credential, transport or outbox logic. Do not enable automatic sync until approved task/conversation/skill adapters and idempotent apply handlers are implemented.

### Sync runtime tests

Run:
`python -m unittest tests.test_sync_runtime tests.test_sync_client tests.test_sync_agent tests.test_sync_auth_api -v`

These tests are authored but have not been executed as part of this implementation session.

## First domain adapter: explicit task export

The CLI now supports `/sync tasks preview` and `/sync tasks queue`.

- Preview is read-only and reports eligible local tasks without queueing or uploading anything.
- Queue requires a paired device and explicit user action.
- Only task description (max 500 characters), completion state and creation timestamp are exported. Local numeric IDs and all other task fields are excluded.
- Events are revisioned and placed in the durable local outbox. Queueing does not make a network request.
- `/sync run` remains disabled until remote task application has a conflict policy and idempotent local handler.

Task events use a deterministic per-device object identity and event ID so a retry after a process interruption can reproduce the same event. The local `_yuki_sync` metadata is stored with the task JSON. If the metadata file cannot be saved after queueing, the CLI warns and asks the user to retry before editing the affected task; a full transactional bridge between task storage and outbox remains future work.

Run the new tests with:
`python -m unittest tests.test_task_sync_adapter tests.test_task_sync_outbox_integration -v`

These tests have been added but have not yet been executed in this environment.

## Task-only sync execution

The CLI `/sync run` now invokes the bounded `SyncAgent` cycle with the task-domain adapter as its remote apply handler. It requires a paired device and configured HTTPS hub.

Supported remote operation: `task_records` upserts with exactly `description`, `completed` and `created_at`. A new remote task receives a local numeric ID while retaining its opaque shared object ID. Subsequent revisions update the existing local task after a strict next-revision check. Duplicate deliveries are idempotent; stale revisions are ignored; revision gaps or same-revision/different-content payloads stop the cycle.

Deletes and all other collections are rejected. A rejected remote event is not acknowledged and the download cursor does not advance, so the user must resolve unsupported events before the device can continue past them. This is intentionally safer than silently skipping data.

Task identity is preserved when a receiving device later edits and re-queues the task. The task's local numeric ID is not shared. Concurrent edits from multiple devices can still produce revision conflicts; there is no automatic last-write-wins merge.

Run the focused regression suite with:
`python -m unittest tests.test_task_sync_adapter tests.test_task_sync_outbox_integration tests.test_remote_task_apply tests.test_sync_agent -v`

Tests are authored but have not been executed in this implementation environment. End-to-end acceptance on Windows and Linux remains required.

## Shared local data root (`YUKI_HOME`)

Yuki task persistence now uses the same portable data-root resolver as the sync runtime. Set `YUKI_HOME` to an absolute writable directory on Windows or Linux to keep task data, local sync outbox and other portable state outside the source/install directory. The default remains the repository root for backwards compatibility.

Windows PowerShell example:
```powershell
$env:YUKI_HOME = "$env:LOCALAPPDATA\Yuki"
python .\yuki.py
```

Linux example:
```bash
export YUKI_HOME="$HOME/.local/share/yuki"
python ./yuki.py
```

Use the same local path for the CLI and desktop on a given machine. Do not point two machines at a shared network filesystem; cross-device sharing must go through the authenticated sync hub. `YUKI_HOME` is local state, not a cloud mount.

Regression coverage: `tests/test_yuki_home_task_path.py` checks the configured path and backwards-compatible default.

## Data-only skill-pack synchronization

The core/skill_pack_sync.py adapter synchronizes only validated JSON skill-pack manifests from Yuki's installed data-only skill-pack directory. The CLI exposes /sync skills preview and /sync skills queue; queueing adds events to the local outbox but does not upload until /sync run.

Security boundaries:
- Python modules, shell scripts, executable plugins, arbitrary skill directories, credentials and approval grants are never transferred.
- The receiver validates the manifest and writes it atomically under the shared YUKI_HOME/skill_packs directory.
- Every remotely received pack is forced to high risk, regardless of its sender's risk metadata. The local guarded skill runtime still requires approval.
- Event identity and exact event envelope are persisted locally so retries remain idempotent.
- Unversioned edits to a previously queued skill are rejected; skill update/revision and deletion/tombstone support are not implemented.
- A conflicting local pack is never overwritten automatically.

After a successful sync, the existing SkillRegistry.defaults() loader discovers the received pack on the next CLI command. The desktop must use the same registry and shared YUKI_HOME to see the same pack; desktop wiring is not yet implemented.

The sync cycle now supports task upserts, conversation metadata/messages, and data-only skill metadata. Executable plugins, preferences, approval state, deletions, skill edits and message edits remain unsupported.
