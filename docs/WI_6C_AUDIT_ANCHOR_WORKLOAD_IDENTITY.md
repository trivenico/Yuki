# WI-6C — Audit-anchor workload identity

## Credential profile

`core.audit_anchor_identity.AuditAnchorCredentialAuthenticator` verifies a dedicated compact-JWS profile for the independent audit-anchor endpoint. It deliberately does not call `Ed25519WorkloadCredentialVerifier`: that verifier requires a task ID and lease generation, neither of which is a truthful binding for a standalone audit publisher service. Reusing it with fabricated task/lease values would weaken the meaning of those claims.

The anchor profile requires exactly these signed claims:

- `iss`: configured issuer identifier
- `sub`: publisher identity registered in the anchor service
- `aud`: exact configured anchor endpoint audience
- `iat`, `nbf`, `exp`: integer validity timestamps
- `jti`: unique credential identifier
- `scope`: unique list of allowed anchor scopes

The protected header is exactly `typ=yuki-audit-anchor+jwt`, `alg=EdDSA`, and `kid`. Ed25519 public keys are resolved through the existing `WorkloadPublicKeyResolver` contract. The authenticator rejects unknown/retired keys, malformed or duplicate JSON members, invalid signatures, wrong audience, unsupported scopes, expired credentials, and replay/revocation state failures. Maximum lifetime is five minutes and clock skew is bounded to thirty seconds.

The profile supports `audit-anchor:read`, `audit-anchor:publish`, and a separately reserved `audit-anchor:publishers:admin` scope. The publisher ASGI adapter maps GET to read and PUT to publish and rejects other methods. Each credential is one-use: the host must issue a fresh credential for each HTTP request. Replay state must be shared, durable, atomic, and fail closed across all service instances.

## Key lifecycle and rotation

The verifier owns no private key material. A deployment's resolver is the trust and rotation control:
1. Provision a new Ed25519 public key under a new `kid` in the trusted issuer registry.
2. Begin signing with the new key only after all verifier instances can resolve it.
3. Retain the previous public key only for the explicitly approved overlap window.
4. Remove/disable the old `kid` to reject credentials signed by the retired key.
5. Revoke individual `jti` values through the credential-state backend when needed.

Signing keys must remain in a KMS/HSM or equivalent non-exportable signer. Never store private signing keys in the anchor SQLite database, environment files, source control, or Yuki task payloads. Do not reuse OAuth encryption keys or unrelated symmetric secrets as Ed25519 keys.

## Administrator separation

Publisher credentials are not administrator credentials. The existing `SQLiteAuditAnchorService.set_publisher` operation still requires the independent host's `authorize_administrator` callback. This change does not expose publisher enrollment/revocation over the publisher API and does not make the anchor's local administrative table immutable. A deployment must expose administration only through a separate authenticated control plane, with its own audience and administrator identity provider, and must preserve/forward the administrative audit records to independently controlled monitoring or storage.

The reserved admin scope is not, by itself, an implemented admin API. Do not grant it to publishers or interpret its presence as permission to call `set_publisher`.

## Integration

Configure the service with an authenticator instance and pass its `authenticate_scope` method as `AuditAnchorASGI(authenticate_scope=...)`. The resolver and credential-state backend must be the same trusted, operationally managed identity infrastructure used by the workload security system, with an audience unique to this anchor deployment.

The existing task-bound issuer is intentionally not reused: anchor service identities need a service audience and no task/lease claims. A deployment-owned issuance adapter must authenticate the publisher workload independently and sign this profile using the host signer. This PR supplies verification and the ASGI authentication adapter; it does not provision keys, deploy a KMS, implement a production issuer, or create a hosted anchor service.

## Operational limitations

- This verifier is one part of the trust boundary; the resolver and replay/revocation backend are security-critical dependencies.
- Each HTTP request consumes a credential. Clients must obtain a fresh credential for GET and PUT separately.
- The anchor service's publisher allowlist remains a second authorization layer after cryptographic identity verification.
- The admin API/control plane remains deployment-owned and separate.
- No live credentials, KMS/HSM, CA, hosted service, backup system, or production deployment are configured by this code.
- Tests are added but must be run in CI and in the deployment's integration environment before rollout.
