# Phase 108 — Local device pairing and scoped credentials

## Scope

Adds `core/device_pairing.py`, a SQLite-backed primitive for one-time device pairing,
scoped authorization, revocation, and an audit trail. The module is not connected to
a network listener or the application runtime.

## Security properties

- Pairing codes are generated with a cryptographically secure random generator and
  only their SHA-256 hashes are persisted.
- Pairing challenges expire (30 seconds to 15 minutes) and are consumed once.
- A successful pairing returns a high-entropy device credential once; only its hash
  is stored.
- Device scopes are allowlisted and limited to sync event/conflict read/write.
- Authentication checks the credential, revocation status, and required scope.
- Revocation is persistent and blocks subsequent authentication.
- Audit records omit pairing codes and device credentials.

## Operational boundaries

- This is a storage/authentication primitive, not a complete pairing protocol.
- It does not deliver the pairing code out-of-band, establish peer identity over a
  transport, encrypt traffic, rate-limit network attempts, or protect a compromised
  host/database.
- Do not expose this store through an unauthenticated public endpoint. The eventual
  transport must use TLS, peer authentication, rate limits, and explicit user
  authorization.
- Credentials should be shown to the operator only once and stored securely by the
  calling application.

## Verification

```bash
python -m unittest tests.test_device_pairing -v
python -m unittest discover -s tests -v
```

The focused tests cover single-use challenges, wrong codes, credential storage,
scope enforcement, revocation, audit redaction, and idempotent revocation.
