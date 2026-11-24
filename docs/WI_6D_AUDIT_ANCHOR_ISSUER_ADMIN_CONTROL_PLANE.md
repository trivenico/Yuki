# WI-6D — Audit-anchor credential issuance and administration

## Publisher credential issuance

`core.audit_anchor_credential_issuer.AuditAnchorCredentialIssuer` issues the dedicated `yuki-audit-anchor+jwt` profile introduced in WI-6C. It accepts an opaque bootstrap handle, authenticates it through a host-provided identity source, checks current publisher enrollment through the anchor service registry, and signs a credential with the injected `WorkloadSigner`.

The issuer does not accept a caller-supplied publisher identity, does not grant scopes based on caller claims, and does not issue the reserved administrator scope. Requested scopes must be a non-empty subset of `audit-anchor:read` and `audit-anchor:publish`. Credentials receive a fresh random `jti`, exact configured audience, issuer and active `kid`, and a lifetime capped at five minutes. The private key remains inside the signer adapter.

The bootstrap identity source is security-critical. It must authenticate a workload using a trusted channel (for example, workload mTLS or a host-managed service identity exchange), not trust a user-supplied identity header or a value copied from the request body. The issuer checks enrollment immediately before signing; the anchor service also checks its publisher allowlist on every read/write.

## Administrator control plane

`core.audit_anchor_admin.AuditAnchorAdminASGI` is a separate ASGI application with:
- `PUT /v1/admin/publishers/{publisher_identity}` — enroll or re-enable a publisher.
- `DELETE /v1/admin/publishers/{publisher_identity}` — revoke publisher enrollment.
- `GET /v1/admin/audit` — read publisher administration audit records.

The app receives a separate host-provided `authenticate_admin_scope` callback. The resulting identity is checked again by the anchor service's `authorize_administrator` callback before any operation. Publisher credentials and the publisher API do not expose these routes. Mount the admin app on a separately protected listener/host or behind an independently authenticated administrative gateway; do not expose it on the public publisher route without distinct network and identity policy.

Enrollment and revocation continue to be recorded transactionally in the anchor database. That local table is an operational audit record, not immutable/tamper-proof storage; forward it to independently controlled monitoring or append-only storage.

## Key rotation and revocation

The signer adapter owns signing-key lifecycle. Provision and distribute a new public key in the WI-6C resolver before switching the active signer key. Retain old public keys only for a documented overlap window, then retire their key IDs. Revoke an individual credential by its issuer and `jti` in the shared credential-state backend. Revoke a publisher through the administrator API; the anchor service's publisher allowlist blocks future requests even if a previously issued credential has not expired.

## Deployment wiring

- Configure the issuer with the same anchor audience used by `AuditAnchorCredentialAuthenticator`.
- Inject a non-exportable KMS/HSM-backed `WorkloadSigner`.
- Inject a trusted bootstrap identity source that returns only authenticated publisher identities.
- Use a durable, shared, atomic replay/revocation backend for the verifier.
- Mount `AuditAnchorASGI` and `AuditAnchorAdminASGI` behind separate publisher/admin identity boundaries.
- Keep publisher and administrator identities, audiences, listeners, and authorization policies distinct.

This PR does not provision a real KMS/HSM, CA, bootstrap identity provider, shared replay database, or hosted deployment. It provides provider-neutral boundaries and ASGI application code; deployment owners must implement and test the host adapters and network separation before production use.
