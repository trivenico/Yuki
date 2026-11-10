# Phase 160 — Authenticated device inference API

## Included

- Owner-session endpoint to mint a short-lived, single-use pairing challenge.
- Device pairing endpoint that consumes the challenge and returns the device bearer credential once.
- Owner-scoped active-device inventory and revocation.
- Owner-scoped inference job creation, listing, lookup, and cancellation.
- Device-token-only worker claim and completion endpoints, compatible with the existing `RemoteInferenceJobClient` contract:
  - `POST /v1/inference/claim`
  - `POST /v1/inference/jobs/{job_id}/complete`
- Compatibility aliases for the explicit worker route namespace:
  - `POST /v1/inference/worker/jobs/claim`
  - `POST /v1/inference/worker/jobs/{job_id}/complete`
- Runtime wiring in `scripts/run_web.py`; device pairing and inference jobs share `inference.sqlite3`.

## API trust boundaries

| Endpoint | Credential | Purpose |
| --- | --- | --- |
| `POST /v1/devices/pairing-challenges` | Browser session | Create owner-bound, expiring pairing code |
| `POST /v1/devices/pair` | Single-use pairing code | Pair device and return device token once |
| `GET /v1/devices` | Browser session | List only the current owner's active devices |
| `DELETE /v1/devices/{device_id}` | Browser session | Revoke only the current owner's device |
| `POST /v1/inference/jobs` | Browser session | Queue work for an owned active device |
| `GET /v1/inference/jobs` | Browser session | List the current owner's jobs |
| `GET /v1/inference/jobs/{job_id}` | Browser session | Read one owned job |
| `POST /v1/inference/jobs/{job_id}/cancel` | Browser session | Cancel an owned queued/running job |
| `POST /v1/inference/claim` | Device bearer token | Claim the next job assigned to this device |
| `POST /v1/inference/jobs/{job_id}/complete` | Device bearer token + lease | Complete/fail only the device's current leased job |

Owner IDs are derived from the authenticated session. Device credentials cannot access owner routes, and browser sessions cannot claim worker jobs. Owner-facing job projections omit input text and lease tokens. Worker claim returns only the fields required by the existing remote worker client.

## Revocation and transaction model

The pairing and job services must use the same SQLite database. Job creation, worker claim, and worker completion begin an immediate transaction and check the current device credential/ownership inside that transaction. Device revocation writes to the same database. SQLite write serialization therefore establishes a clear ordering: a claim/completion committed before revocation is valid; once revocation commits, subsequent claims/completions with that credential are rejected. Lease fencing continues to reject stale worker completions after reclaim or cancellation.

## Local use

The web launcher creates `inference.sqlite3` in the configured workspace automatically. Existing authenticated web chat setup from [Phase 159](PHASE159_AUTHENTICATED_WEB_CHAT.md) is unchanged.

Pair a worker by creating a challenge from the authenticated owner session, then submit the code and a device name from the worker. Store the returned device token in the worker's local secret store; it is never shown again by the API.

## Remaining production work

This phase exposes the authenticated job transport; it does not by itself provide a hardened public deployment or complete remote-worker operations. Before internet-facing production, add deployment-specific request rate limits, trusted-host/origin controls, structured security telemetry, backup/restore verification, and operational monitoring. The worker process still needs supervised lifecycle management, bounded concurrency, health reporting, and end-to-end tests against a real local/remote model runtime. HTTPS is mandatory for non-loopback access.
