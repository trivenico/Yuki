# WI-6J — Workload key control-plane integration

## Shared trust resolver

The existing `Ed25519WorkloadCredentialVerifier` and
`AuditAnchorCredentialAuthenticator` already depend on the
`WorkloadPublicKeyResolver` protocol. Supply the same
`SQLiteWorkloadKeyRegistry` instance (or the same strongly consistent
production resolver implementation) to both verifier constructors. This
makes staged/active/retired/revoked lifecycle state authoritative at every
signature verification; no key snapshots or token-supplied key sources are
introduced.

A resolver outage fails closed. Revocation and retirement are observed on the
next verification because the registry is queried live for every credential.
Already-authenticated requests are not retroactively cancelled; request/task
authorization and lease checks remain responsible for ongoing operations.

## Administrator API

`WorkloadKeyAdminASGI` is a separate control-plane application. It supports:

- `PUT /v1/admin/keys/{issuer}/{kid}` with JSON `{"algorithm":"EdDSA","public_key":"<base64 raw 32-byte key>"}`
- `POST /v1/admin/keys/{issuer}/{kid}/activate`
- `POST /v1/admin/keys/{issuer}/{kid}/retire`
- `POST /v1/admin/keys/{issuer}/{kid}/revoke`
- `GET /v1/admin/keys/audit`

The host-provided `authenticate_admin(scope)` callback must return a trusted
administrator identity or a non-string/empty value. The registry performs a
second authorization check for that identity on every operation. The app
rejects duplicate JSON members, unexpected request bodies, non-EdDSA keys,
oversized bodies, and non-canonical/incorrect-length public key material.
Responses are non-cacheable and do not expose internal exception details.

Mount this app only behind an independently authenticated administrator
listener/network policy. Do not mount it on the publisher or public chat
listener. Authentication callback configuration is deployment-owned.

## Rotation and revocation

Register and activate a replacement key before changing the signer. Keep
both public keys active during the bounded overlap. Retire the old key only
after all credentials it signed have expired plus skew/propagation margin.
Emergency revocation is immediately effective for both verifier profiles.
The integration tests exercise live resolver lookups and rotation/revocation
against both credential authenticators.

This is still a single-host SQLite implementation. Multi-host production
requires a shared strongly consistent registry and revocation propagation.
Private keys remain in the host signer/KMS/HSM; this control plane accepts
public keys only. Registry audit events must be forwarded to the independent
audit domain. No deployment, KMS, real identity provider, or fleet rollout is
created by this change.

Tests are added but were not executed locally in this connector-only session.
