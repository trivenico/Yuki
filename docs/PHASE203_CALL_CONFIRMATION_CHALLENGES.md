# One-time owner confirmation challenges for LAN calls

## Scope

`core/call_confirmation.py` provides durable, one-time confirmation challenges. `api/inference.py` now exposes an authenticated challenge-issuance endpoint and consumes the challenge before creating a LAN call invitation. The owner is derived from the authenticated session, and issuance requires a fresh password check against that same active session.

The repository now includes an owner-facing browser flow in `ui/web`: it lists paired devices, displays the exact caller and recipient IDs/names in a confirmation prompt, then re-authenticates and immediately consumes the challenge. This improves the human-consent boundary but does not make a browser prompt cryptographic proof of a trusted gesture; the server still enforces session, password, ownership, expiry, and one-time challenge checks. Do not expose the owner password to models, agents, background workers, or tools.

## API flow

1. The owner UI authenticates using the existing Yuki session and loads the owner's active paired devices.
2. The owner selects a caller and recipient. The UI displays both device names and IDs and asks for explicit confirmation before requesting a challenge.
3. The owner enters the account password. The server revalidates the active session and password, then verifies both devices are active and owned by that account.
4. The server returns a random challenge bound to the owner, current session, caller device, and recipient device.
5. The UI immediately submits the exact device pair and challenge token. The invitation endpoint atomically consumes the token before persisting the invitation; a failed or replayed token cannot create a second call. The password field is cleared after the operation.
6. The recipient device must separately accept the invitation. The owner UI does not impersonate the recipient device.

The API deliberately rejects the former `confirm: true` request shape.

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

## Remaining production gates

- Harden the owner UI for production deployment: accessibility and browser compatibility tests, CSP and dependency review, XSS regression tests, and end-to-end owner/device tests. A confirmation dialog is a human-facing safeguard, not cryptographic proof of a trusted gesture.
- Add transport-level protections appropriate to the deployment (TLS, origin controls/CSRF where cookie sessions are used, rate limits, secret redaction).
- Add notification delivery and authenticated WebRTC signaling/media only after invitation consent is complete.
- Test real devices, reconnects, cancellation, notification delivery, and media permission prompts. No WebRTC, push notification, microphone or camera capability is implied by this API.
