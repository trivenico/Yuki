# One-time owner confirmation challenges for LAN calls

## Scope

`core/call_confirmation.py` provides durable, one-time confirmation challenges. `api/inference.py` now exposes an authenticated challenge-issuance endpoint and consumes the challenge before creating a LAN call invitation. The owner is derived from the authenticated session, and issuance requires a fresh password check against that same active session.

The repository now includes an owner-facing browser flow in `ui/web`: it lists paired devices, displays the exact caller and recipient IDs/names in a confirmation prompt, then re-authenticates and immediately consumes the challenge. After creation, the browser retains only the opaque digest handle and offers an explicit owner-confirmed cancellation control while the invitation is still ringing. This improves the human-consent boundary but does not make a browser prompt cryptographic proof of a trusted gesture; the server still enforces session, password, ownership, expiry, and one-time challenge checks. Do not expose the owner password to models, agents, background workers, or tools.

## API flow

1. The owner UI authenticates using the existing Yuki session and loads the owner's active paired devices.
2. The owner selects a caller and recipient. The UI displays both device names and IDs and asks for explicit confirmation before requesting a challenge.
3. The owner enters the account password. The server revalidates the active session and password, then verifies both devices are active and owned by that account.
4. The server returns a random challenge bound to the owner, current session, caller device, and recipient device.
5. The UI immediately submits the exact device pair and challenge token. The invitation endpoint atomically consumes the token before persisting the invitation; a failed or replayed token cannot create a second call. The password field is cleared after the operation.
6. The recipient device must separately accept the invitation. `automation/remote_call_client.py` provides an authenticated HTTPS inbox client, and `automation/call_inbox.py` consumes the durable notification inbox and provides a foreground-only terminal prompt with explicit accept/decline/ignore choices. Accept/decline is completed before the alert is acknowledged; ignored alerts remain pending and are deduplicated for the lifetime of the watcher process. The owner UI does not impersonate the recipient device. The CLI supports `yuki calls-inbox --api-url https://your-yuki-server --watch` for a foreground watcher (three-second polling by default; `--poll-interval 1..30` adjusts it). The watcher remembers notification IDs already presented during that process and never auto-accepts. It is not a push-notification service or background daemon.

The API deliberately rejects the former `confirm: true` request shape. The authenticated owner can inspect a bounded newest-first history with `GET /v1/devices/calls` (up to 50 entries); this projection contains only opaque digest handles and call metadata, never bearer credentials or the original random session secret. The authenticated owner may also cancel a still-ringing invitation with `POST /v1/devices/calls/{opaque_session_handle}/cancel`; cancellation is owner-scoped, cannot cancel an accepted call, and immediately removes the invite from the recipient inbox. The opaque handle is the 64-character SHA-256 session digest returned by the API, not the original random session secret.

## Security properties

- Random 256-bit URL-safe challenge token; only its SHA-256 digest is stored.
- 15–300 second configurable expiry (90 seconds by default).
- Bound to one owner, one authenticated session and one exact caller/recipient pair.
- Re-authentication requires a currently active, unrevoked, unexpired session and the account password; failed checks increment the account lockout counter.
- Re-authentication lockout updates serialize under SQLite `BEGIN IMMEDIATE`.
- A mismatched attempt does not consume a legitimate challenge.
- SQLite `BEGIN IMMEDIATE` and conditional update enforce single-use behavior under concurrent/replayed requests.
- At most ten outstanding challenges per owner; expired and consumed rows are cleaned up on issuance.
- Device bearer credentials cannot invoke owner endpoints; owner sessions cannot invoke recipient device actions.
- The recipient inbox filters invitations whose caller device has since been revoked, and acceptance rechecks caller-device activity inside the call-state transaction. Revoked recipient credentials are rejected by the same paired-device authentication boundary for both inbox retrieval and invitation actions.

## Remaining production gates

- Harden the owner UI for production deployment: accessibility and browser compatibility tests, CSP and dependency review, XSS regression tests, and end-to-end owner/device tests. A confirmation dialog is a human-facing safeguard, not cryptographic proof of a trusted gesture.
- Add transport-level protections appropriate to the deployment (TLS, origin controls/CSRF where cookie sessions are used, rate limits, secret redaction).
- Add OS-level notification adapters and authenticated WebRTC signaling/media only after invitation consent is complete.
- Test real devices, reconnects, cancellation, notification delivery, and media permission prompts. No WebRTC, push notification, microphone or camera capability is implied by this API.


## Durable paired-device notification inbox

Call invitation creation now writes a minimal `incoming_call` notification row in
the same SQLite transaction as the ringing session. This is a durable outbox
boundary, not an OS push integration. Paired devices poll
`GET /v1/devices/calls/notifications/inbox` with their device bearer credential
and acknowledge an item with
`POST /v1/devices/calls/notifications/{notification_id}/ack`.

Delivery is intentionally at-least-once until acknowledgement: a device that
misses an acknowledgement may see the same alert again. The inbox is bounded
to 50 rows per poll and filters expired/cancelled calls, revoked caller devices,
and non-ringing sessions at read time. Expired notification rows are pruned during inbox polling so offline devices cannot create an unbounded stale-alert backlog. Acknowledgement is recipient-device and
owner scoped. Notification identifiers and call handles are opaque digests;
the event contains no call secret, password, media token, or message content.
The alert is only a prompt to open the call UI. Recipient acceptance remains a
separate authenticated action; no media device is activated.

This does not yet provide background delivery, OS notifications, lease-based
worker dispatch, guaranteed exactly-once display, or WebRTC. The foreground CLI
watcher remains the current polling client.


The paired-device CLI client now polls the durable notification endpoint rather
than the legacy invitation listing endpoint. It acknowledges an alert only
after an explicit accept or decline has succeeded. If the user ignores an alert,
it remains unacknowledged and can be presented again after the process restarts.
A failed acknowledgement is surfaced rather than reported as a successful
delivery. If an accept/decline response is lost or times out, the client reads
the participant-scoped call state before deciding what happened: it treats the
action as complete only if the requested terminal state is present, and does
not blindly replay a transition. If the server still reports `ringing`, the
original error is surfaced and the notification remains unacknowledged.

Acknowledgement is idempotent for the same authenticated recipient device:
repeating an acknowledgement after a lost response returns success when that
same device had already acknowledged the alert. This makes acknowledgement
safe to retry without changing the invitation's accept/decline state.
