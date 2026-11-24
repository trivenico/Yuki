# WI-6G — Audit-anchor end-to-end integration

## Scope

This work item connects the existing enrollment, credential issuance, credential verification, durable replay/revocation state, and independent checkpoint service through their actual ASGI boundaries. It is an integration test milestone; it does not claim to deploy or provision the trust infrastructure.

## Verified flow covered by the integration test

1. An authenticated administrator enrolls a publisher through `AuditAnchorAdminASGI`.
2. `AuditAnchorCredentialIssuer` authenticates an opaque host bootstrap handle and checks the live publisher registry before issuing a short-lived Ed25519 credential.
3. `AuditAnchorCredentialAuthenticator` verifies the credential and uses `SQLiteAuditAnchorCredentialState` to enforce durable one-use consumption.
4. `AuditAnchorASGI` derives publisher identity only from the authenticated credential and the service independently checks current enrollment before serving or updating a checkpoint.
5. Reusing a consumed credential is rejected; an outstanding credential issued before publisher revocation is rejected by the service's live enrollment check.
6. Key rotation removes trust in the retired key and allows credentials signed by the newly active key.
7. Administrative enrollment and revocation remain separately authenticated and appear in the administrative audit trail.

## Security and deployment boundaries

- The test composes distinct publisher and administrator ASGI applications. The host must still mount them behind separate protected listeners or independently authenticated gateways; Python object composition cannot enforce network isolation.
- The bootstrap identity source, administrator authentication, signing-key custody, and public-key distribution remain host-provided dependencies.
- SQLite replay state and anchor state are single-host starter backends. Multi-host deployments need shared transactional implementations of the relevant protocols.
- The integration test uses generated in-process Ed25519 keys and temporary SQLite databases. It does not exercise production KMS/HSM, mTLS termination, secret distribution, backup storage, or real deployment networking.
- Tests are committed but have not been executed in this environment.

## Operational acceptance before production

Run the full test suite and CI; configure protected, distinct admin and publisher ingress; provide production signing and verification key rotation; use a shared durable replay/revocation backend when horizontally scaled; validate backup restore and revocation reconciliation; and monitor readiness, publisher failures, and checkpoint freshness.
