# WI-6I — Production workload signing-key lifecycle

## Scope

WI-6I adds `SQLiteWorkloadKeyRegistry`, an implementation of the existing
`WorkloadPublicKeyResolver` contract. It stores Ed25519 public-key metadata
and lifecycle state. It never accepts or stores private signing material;
credential signing remains the responsibility of the injected
`WorkloadSigner` implementation and its host/KMS/HSM custody boundary.

## Lifecycle

- **Register:** adds a unique issuer/key ID as `staged`. Re-registering an
  existing ID is rejected; key material cannot be silently replaced.
- **Activate:** only staged keys can become active. Multiple active keys per
  issuer are supported to permit an explicit rotation overlap.
- **Retire:** removes an active key from verification. Retirement is terminal
  and is refused when it would leave an issuer with no active key.
- **Emergency revoke:** immediately removes an active key from resolution,
  including when it is the last active key. Revocation is terminal.
- **Resolve:** returns only an active EdDSA key for the exact issuer and key ID.
  Unknown, staged, retired, revoked, and unsupported-algorithm lookups return
  no key and therefore fail closed in existing verifiers.

Every lifecycle mutation runs in a SQLite write transaction and appends an
administrative event. Audit rows have database triggers preventing update or
delete through this registry database connection. The injected administrator
authorization callback must resolve a trusted host identity and return literal
`True`; caller-supplied identity strings alone are not authentication.

## Operational rotation

1. Generate a new Ed25519 key in the host KMS/HSM; do not export its private key.
2. Register the new key's public bytes under a never-before-used key ID.
3. Activate the new key and configure the host signer to begin signing with it.
4. Keep the previous public key active for the maximum credential lifetime plus
   clock-skew and deployment propagation margin.
5. Retire the previous key after outstanding credentials can no longer be valid.
6. For suspected compromise, revoke immediately; availability may be sacrificed
   rather than accepting signatures from the compromised key.

The registry does not itself coordinate signer cutover. Deployment automation
must ensure the signer never emits a key ID before that public key is activated
on every verifier that must accept it.

## Deployment boundaries and limitations

This SQLite implementation is a single-host/local-filesystem starter. It is
not a multi-host, multi-region, network-filesystem or strongly replicated trust
service. Production fleets need a shared transactional registry or managed
trust distribution with consistent revocation propagation. The administrator
callback must be backed by a real identity provider and protected control-plane
listener. SQLite owner-only permissions are defense in depth, not encryption
or protection from another process running as the same OS account.

The audit table is append-only within this database, not independently
tamper-proof against a privileged database/file administrator. Forward events
to the independent authorization audit anchor or an immutable external log.
KMS/HSM provisioning, signer rotation orchestration, verifier fleet rollout,
key backup/recovery, alerting and compromise drills remain deployment work.

Tests are added in `tests/test_workload_key_registry.py`; they were not run
in this connector-only implementation session.
