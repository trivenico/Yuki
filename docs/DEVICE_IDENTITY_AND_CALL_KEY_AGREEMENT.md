# Paired-device cryptographic identity and call key agreement

**Status:** Proposed protocol; not implemented.  
**Scope:** Prerequisite design for authenticated LAN call signaling and future device-to-device control.  
**Current baseline:** Paired devices authenticate to Yuki using revocable bearer tokens. The pairing service does not yet store device public keys, prove possession of a private key, or establish end-to-end media keys.

## Decision summary

Use separate, device-held asymmetric keys for identity and key agreement:

- **Identity/signatures:** Ed25519 key pair generated on the device. The private key is non-exportable where the platform supports a secure hardware-backed keystore; otherwise it is stored using the OS credential/keystore API with documented protection limits.
- **Key agreement:** X25519 ephemeral key pairs per call/session. Never reuse an ephemeral private key across sessions.
- **Key derivation:** HKDF-SHA-256 with an explicit protocol/version label and transcript hash.
- **Authenticated encryption:** ChaCha20-Poly1305 or AES-256-GCM, selected from a versioned, reviewed cipher suite. Never invent a custom cipher or nonce scheme.
- **Transport:** TLS for device-to-Yuki API traffic; end-to-end encryption remains mandatory for signaling/media payloads even when TLS is used.
- **Serialization:** A strict, versioned canonical encoding with duplicate-key rejection and bounded field lengths. Sign the canonical transcript bytes, not an ad hoc JSON rendering.

The implementation must use a maintained cryptographic library and a reviewed handshake construction. This document is an architecture contract, not a claim that the protocol has been implemented or independently reviewed.

## Security goals

1. A paired device has a stable identity that is cryptographically bound to its owner and device record.
2. A remote party can prove possession of the private identity key; knowledge of a device ID or bearer token alone is not proof of device-key possession.
3. A call's ephemeral key agreement is bound to the exact accepted call session, both participant identities, protocol version, selected cipher suite and both ephemeral public keys.
4. A relay/server can route ciphertext but cannot derive call encryption keys or read SDP, ICE candidates, media keys, chat payloads or other end-to-end protected content.
5. Revocation, key rotation, replay, downgrade, stale sessions and duplicate messages fail closed.
6. No microphone, camera, screen capture or external device action starts as a side effect of pairing, key exchange, invitation receipt or cryptographic handshake.

## Threat model and limits

Protect against network observers, on-path modification, replay, session-handle substitution, unauthorized paired devices, stolen bearer tokens without the private device key, revoked devices and a compromised signaling relay.

This design does **not** protect against a compromised endpoint while its private key or plaintext is in use, a malicious OS/firmware, a user approving the wrong device, or a fully compromised Yuki server that can misrepresent the owner/device registry. Owner-visible key fingerprints and out-of-band verification are required for stronger protection against server-side key substitution. Hardware-backed key custody is platform-dependent and must be reported honestly.

LAN membership, an IP address, mDNS/DNS-SD discovery, a device name, or possession of a session handle is never an identity or authorization credential.

## Device identity enrollment

### Enrollment prerequisites

- The device generates its Ed25519 private key locally. The private key must never be sent to Yuki.
- The device exports only the public key and a key identifier derived from a domain-separated hash of the canonical public-key bytes.
- The owner must explicitly approve adding the key to the exact paired-device record. Existing account authentication and reauthentication rules apply.
- The server issues a short-lived, single-use registration challenge bound to owner ID, device ID, current device credential generation, public-key fingerprint, challenge ID, expiry and protocol version.
- The device signs the canonical challenge transcript with its Ed25519 private key. The server verifies the signature against the submitted public key before atomically activating that key.
- Challenge consumption and key activation are one transaction. Challenges are single-use, short-lived, rate-limited, and invalidated on device revocation or credential-generation change.
- The UI shows device name, device ID, key fingerprint, key creation time and custody type (hardware-backed, OS-keystore, or software fallback). Never display private key material.

A pairing bearer token may authorize access to the registration endpoint, but it is not sufficient by itself to activate a public key. Proof of possession and owner approval are both required.

### Stored key metadata

Persist only public and lifecycle metadata:

- owner ID and device ID;
- key ID, algorithm, public key bytes and fingerprint;
- created/activated/rotated/revoked timestamps;
- key status (pending, active, superseded, revoked);
- monotonically increasing key generation;
- proof-of-possession challenge identifier and verification timestamp;
- optional platform keystore/custody capability label.

Never store device private keys, raw registration challenge secrets, session ephemeral private keys or derived call keys on the server. Backups must preserve key metadata integrity but must not become a private-key export mechanism.

### Rotation and revocation

- Rotation requires proof of possession by the new key and authorization by the currently active device key or an explicit owner reauthentication/reapproval path.
- A rotation transaction increments the device key generation and invalidates outstanding registration challenges and call handshakes using the prior generation.
- Revocation immediately blocks bearer authentication and cryptographic session establishment for that device. Existing calls involving a revoked device are terminated or cryptographically invalidated.
- Keep a minimal, append-only audit record containing key IDs/fingerprints and lifecycle event metadata; never include private material or plaintext session content.
- Re-enrollment after revocation creates a new key generation. A previous key must not silently become active again.

## Call handshake

The handshake starts only after the call invitation is accepted by the recipient and the server confirms that both devices remain active, paired to the same owner, and authorized for that session.

1. Each endpoint fetches the session's participant identities, active key IDs/generations, status and expiry over authenticated TLS.
2. Each endpoint creates a fresh X25519 ephemeral key pair for this call. Ephemeral private keys remain only in endpoint memory and are destroyed when the handshake ends.
3. Endpoints exchange ephemeral public keys and signed handshake transcripts through the authenticated signaling service. Each signature covers protocol version, session digest, caller and recipient device IDs, both active identity key IDs/generations, both ephemeral public keys, role, cipher suite, expiry and a fresh random nonce.
4. Each endpoint verifies the peer's identity signature, expected role, exact session binding, active key generation, expiry, cipher suite and transcript hash. Unknown, revoked, mismatched or downgraded keys abort the handshake.
5. Both endpoints derive the shared secret with X25519, then derive independent directional encryption keys and nonce bases with HKDF-SHA-256. Domain-separate keys for signaling, media, and any future data channel. Do not reuse a key/nonce pair across directions or sessions.
6. The endpoints exchange encrypted key-confirmation messages (MAC/AEAD confirmation) over the transcript before the call becomes media-ready.
7. Only after successful key confirmation and a separate explicit user action may the media engine request microphone/camera permission and establish WebRTC media.

Use a reviewed authenticated key-exchange protocol/library if one is available for the target stack; do not hand-roll a bespoke protocol merely to match this outline. The exact wire format, cipher suite and library choice require a separate implementation review before code is merged.

## Signaling relay contract

The relay is a bounded, short-lived ciphertext mailbox, not a trusted plaintext signaling endpoint.

- Accept messages only for an existing, accepted, unexpired call session and only from one of its two active participants.
- Authenticate the device bearer credential and verify the device-key signature for the exact message envelope.
- Bind every envelope to session digest, sender device ID, recipient device ID, sender key ID/generation, message type, monotonically increasing sequence number, creation/expiry timestamps, protocol version and ciphertext.
- Enforce strict per-message and per-session size limits, message-type allowlists, rate limits, maximum mailbox depth and TTL.
- Reject duplicate/replayed sequence numbers, expired messages, stale key generations, unknown types, wrong participants and messages after cancellation/end/revocation.
- Store opaque ciphertext only. Do not log payloads, SDP, ICE candidates, keys, bearer tokens or decrypted content. Logs may include bounded metadata and opaque digests.
- Provide idempotent delivery/acknowledgement semantics without permitting state-transition replay.
- Delete expired mailbox entries and bound cleanup work.
- Never treat signaling delivery as consent to answer a call or activate media.

## WebRTC and media boundary

- Use WebRTC's DTLS-SRTP media protection and authenticated signaling. If application-level end-to-end media keying is required beyond the WebRTC trust model, integrate a reviewed insertable-streams/SFrame-capable design rather than inventing one.
- Never log SDP or ICE candidates. Treat them as sensitive network metadata.
- Restrict ICE candidate exposure and relay policy to the product's documented LAN/TURN behavior; do not silently expose private network topology to unrelated devices.
- Request microphone/camera permissions only after explicit accept and a distinct Start audio/video action. Provide visible permission, mute, camera-off and end-call controls.
- Incoming calls never auto-answer. Revocation, session expiry, key mismatch or failed key confirmation stops media setup.
- Test LAN-only, NAT traversal, relay fallback, network change, sleep/resume, device lock, permission denial, device revocation and abrupt disconnect on supported OS versions.

## Required API/service boundaries

Implement in separate, testable services rather than adding cryptographic behavior to the existing call-session store:

1. Device key registry: owner-scoped enrollment, proof-of-possession, rotation, revocation and key-generation reads.
2. Handshake service: session-bound ephemeral exchange, transcript verification, key confirmation and expiry.
3. Signaling mailbox: authenticated participant routing of signed ciphertext envelopes with replay/size/TTL limits.
4. Native device agent: secure key custody, local user-presence and media permission UX, reconnect and update lifecycle.
5. WebRTC adapter: media setup only after consent and successful cryptographic handshake.

The current `DevicePairingService` and `LANCallSessionStore` remain bearer-authenticated pairing and invitation-state components until these separate services are implemented and integrated.

## Acceptance gates before signaling or media is called production-ready

- Protocol and cryptographic library selection reviewed by an independent security reviewer.
- Known-answer tests for key generation, signatures, X25519, HKDF and AEAD; cross-platform interoperability vectors.
- Positive/negative tests for signature forgery, key substitution, wrong session/role, downgrade, expired challenge, replay, duplicate sequence, stale generation, revocation races and malformed/oversized payloads.
- Database migration, rollback, backup/restore and key-rotation recovery tests.
- No secret, private key, SDP, ICE or plaintext payload in logs, crash reports or analytics.
- Real-device tests across supported desktop/mobile operating systems and networks.
- User-visible device fingerprint/approval, call consent and media permission UX.
- CI, dependency/SBOM review, operational metrics and incident/revocation runbooks pass on the exact release commit.

## Explicit non-claims

This design does not add device cryptographic identity, key exchange, signaling, WebRTC, push notifications, background device agents, microphone/camera access or working LAN calls. Those capabilities remain unimplemented until the acceptance gates above are met.
