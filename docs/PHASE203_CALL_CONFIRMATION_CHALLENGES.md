# One-time owner confirmation challenges for LAN calls

## Scope

`core/call_confirmation.py` provides a durable primitive for a human-confirmed
call invitation. It does not create calls, authenticate users, render a UI, or
deliver notifications.

The future API integration must follow this order:

1. Resolve the owner from the authenticated Yuki session. Never accept an
   owner ID supplied by the request body.
2. Require a fresh credential check (password re-authentication or an
   equivalent platform-authenticator ceremony) in an owner-controlled UI.
   A long-lived bearer session alone is not a fresh confirmation.
3. Verify both devices are active, paired to that owner, and that the requested
   caller and recipient are exactly the devices displayed to the owner.
4. Issue a challenge bound to the owner ID, hash of the current authenticated
   session, caller device ID, and recipient device ID.
5. Display the exact call intent and recipient to the owner before the
   confirmation action. The confirmation token must be returned only from that
   explicit UI action; model output and background agents must not access this
   issuance path.
6. On invitation creation, atomically consume the token using the same owner,
   session, caller and recipient values. Only after successful consumption may
   the call invitation be persisted.

## Security properties

- Random 256-bit URL-safe challenge token; only its SHA-256 digest is stored.
- 15–300 second configurable expiry (90 seconds by default).
- Challenge is bound to one owner, one authenticated session and one exact
  caller/recipient pair.
- A mismatched attempt does not consume a legitimate challenge.
- SQLite `BEGIN IMMEDIATE` and conditional update enforce single-use behavior
  under concurrent/replayed requests.
- At most ten outstanding challenges per owner; expired and consumed rows are
  cleaned up on issuance.
- No challenge is accepted from a boolean `confirm: true` field.

## Not yet implemented

This commit is deliberately only the persistence/security primitive. The
existing LAN call API branch still has its earlier boolean confirmation
contract and must not be merged as production-safe until it is integrated with
fresh owner re-authentication, the visible confirmation UX, and this atomic
challenge consumption. No WebRTC, push notification, microphone or camera
capability is implied.
