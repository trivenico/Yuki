# Phase 201 — LAN call invitation and consent state

## Implemented

- Durable SQLite call invitation records share the paired-device identity database.
- Caller must authenticate with an active paired-device token and pass a literal owner-confirmation gate to create an invitation.
- Recipient must be an active device paired to the same owner; self-calls and cross-owner invitations are rejected.
- Invitation IDs are high-entropy random values; only their SHA-256 digests are stored.
- Invitations expire after a bounded TTL and active ringing invitations are capped per owner.
- Only the intended recipient device can accept or reject; its authenticated accept action is the recipient's explicit consent.
- Either authenticated participant can end a ringing or accepted session.
- Device revocation is checked transactionally on every read and state transition.
- State transitions are serialized with SQLite immediate transactions; repeated or out-of-order transitions fail closed.

## Deliberate boundary

This is signaling **state only**. It does not deliver push notifications, open network sockets, negotiate WebRTC, access microphones/cameras, or carry audio/video. A later transport must use authenticated signaling, short-lived session-bound credentials, replay protection, audit-before-dispatch, DTLS-SRTP media, platform permission prompts, and explicit mute/end controls. It must never infer recipient consent from LAN presence or model output.

## Remaining production work

- Integrate the store with authenticated owner/device APIs and a notification transport.
- Add durable audit events atomically with state transitions.
- Add WebRTC signaling and media transport with explicit device permissions.
- Implement device agents and cross-platform incoming-call UX.
- Run concurrency, expiry-boundary, revocation-race, and real-device network isolation tests.
