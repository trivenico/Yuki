# Phase 202 — Authenticated LAN call API and atomic audit

## Implemented

- Owner-session route creates invitations only after an explicit confirmation field is supplied by the authenticated owner UI action.
- Owner identity is derived from the authenticated browser session, never from request JSON.
- Caller and recipient device IDs must both be active and paired to that same owner.
- Paired-device bearer endpoints expose a bounded incoming-invitation list and allow the intended recipient to accept/reject.
- The incoming list returns the stored SHA-256 session digest as an opaque recipient handle; the original invitation secret is not disclosed.
- Authenticated participants can read session state and end the session.
- Call invitation and transition audit events are hash chained and committed in the same SQLite transaction as state changes.
- No network listener, notification transport, microphone/camera activation, WebRTC negotiation, or media transport is implemented.

## API outline

- `POST /v1/devices/calls` — owner session; requires caller device ID, recipient device ID, and explicit `confirm: true`.
- `GET /v1/devices/calls/incoming` — paired-device bearer; returns pending invitations for that recipient device.
- `POST /v1/devices/calls/{session_id}/accept|reject|end` — paired-device bearer; only the intended recipient can accept/reject, either participant can end.
- `GET /v1/devices/calls/{session_id}` — paired-device bearer; participant-only state read.

## Security boundary and remaining work

The owner confirmation field is meaningful only when sent by an authenticated owner-controlled UI action; model output must never call this route or manufacture consent. Recipient acceptance remains a separate explicit device action. The incoming-list endpoint is a polling API, not push delivery. Before real calls, add transport authentication, notification delivery, replay/rate controls, platform permission UX, WebRTC DTLS-SRTP, device agents, and real-device network isolation tests.