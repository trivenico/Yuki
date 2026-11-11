# One-time owner confirmation challenges for LAN calls

## Scope

`core/call_confirmation.py` provides durable, one-time confirmation challenges. `api/inference.py` now exposes an authenticated challenge-issuance endpoint and consumes the challenge before creating a LAN call invitation. The owner is derived from the authenticated session, and issuance requires a fresh password check against that same active session.

This is an API/security integration, **not yet a complete human-consent UX**. The repository still needs a trusted owner-facing confirmation surface that shows the exact caller and recipient and initiates the re-authentication and challenge flow. The API cannot infer that a request originated from a trusted visible UI merely because it contains a password and bearer token. Do not expose the owner password to models, agents, background workers, or tools.

## API flow

1. The owner UI authenticates using the existing Yuki session.
2. It requests a challenge with the password and exact caller/recipient IDs. The server revalidates the session and password, then verifies both devices are active and owned by that account.
3. The server returns a random challenge bound to the owner, current session, caller device, and recipient device.
4. The UI must display the exact call intent and recipient and obtain the owner's explicit confirmation before submitting the challenge token.
5. The invitation endpoint atomically consumes the token before persisting the invitation. A failed or replayed token cannot create a second call.
6. The recipient device must separately accept the invitation.

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

- Build a trusted owner UI with explicit intent display and a confirmation action; do not treat API possession of credentials as proof of visible human consent.
- Add transport-level protections appropriate to the deployment (TLS, origin controls/CSRF where cookie sessions are used, rate limits, secret redaction).
- Add notification delivery and authenticated WebRTC signaling/media only after invitation consent is complete.
- Test real devices, reconnects, cancellation, notification delivery, and media permission prompts. No WebRTC, push notification, microphone or camera capability is implied by this API.
