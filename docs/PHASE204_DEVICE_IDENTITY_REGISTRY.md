# Phase 204 — paired-device Ed25519 identity registry

**Status:** Experimental core service; draft implementation, not production-ready.  
**Scope:** General paired-device identity foundation for future authenticated local-network device operations.

## Implemented in this phase

- DeviceIdentityRegistry stores Ed25519 public-key metadata in the existing paired-device SQLite database.
- Owner-scoped challenge creation requires an explicit boolean owner-confirmation argument and an active paired device.
- Each challenge binds the owner, device ID, public-key fingerprint, key generation, expiry, challenge ID, nonce, algorithm and protocol version in deterministic canonical JSON with a domain separator.
- The device must authenticate with its paired-device bearer credential and sign the exact transcript with the corresponding Ed25519 private key.
- Challenge consumption and key activation occur in one BEGIN IMMEDIATE transaction.
- The server stores only public-key material, fingerprints and lifecycle metadata. It never receives a private key.
- A SQLite trigger revokes active identity keys and invalidates pending enrollment challenges in the same transaction that revokes the paired-device bearer credential.
- An owner-scoped read returns only the active public-key record for an active device.

## Deliberate limitations

- This phase is a core service only. It does not add HTTP routes, UI, native key generation/storage, OS-keystore integration, key rotation, authenticated command transport, or device-control execution.
- Enrollment is initial-key-only. A device with an active key cannot replace it through this API. Rotation needs a separately reviewed two-key transition and recovery protocol.
- The owner_confirmed argument is an application boundary, not a UI. Any future API must derive owner identity from the authenticated session and only set confirmation after a real owner approval interaction.
- The bearer credential authenticates the device record; Ed25519 proof separately establishes possession of the submitted private key. Neither alone is sufficient.
- Public-key fingerprints are not an out-of-band identity verification channel. A future UI must show the fingerprint and provide a clear owner verification step.
- Software private-key custody is not implemented. Device keys must be generated and retained locally by a future native agent using platform keystore APIs where available.
- The signature transcript is an enrollment proof, not a general-purpose request-signing or transport protocol. Do not treat enrollment as authorization for device commands.

## Required follow-up before production

1. Independent cryptographic review of the transcript, challenge lifecycle, key-ID derivation and rotation/recovery model.
2. HTTP integration with strict request schemas, owner reauthentication/approval UX, device bearer separation, rate limits and sanitized errors.
3. Native agent implementation for key generation, signing, secure storage, key backup/recovery policy and cross-platform behavior.
4. Key rotation and owner recovery design with race, rollback and revocation tests.
5. Known-answer tests and interoperability vectors on every supported platform.
6. Implement a separately reviewed authenticated command protocol with request signing, freshness/replay protection, capability checks, and per-command audit before enabling remote device actions.

## Test coverage added

The test suite covers owner approval and ownership, private-key proof, wrong-key signatures, single-use and nonce binding, cross-device credential rejection, active-key overwrite rejection, owner-scoped reads, and atomic key invalidation on device revocation.

CI results must be checked on the exact pull-request head. No local test execution or independent security certification is claimed by this change.