# Phase 109 — Authenticated sync request envelope

## Scope

Adds a reusable request-signing and replay-protection primitive for a future sync
transport. It does not create an HTTP server, open sockets, or automatically send
events.

## Security controls

- Canonical HMAC-SHA256 signatures bind the device ID, HTTP method, request path,
  timestamp, nonce, and SHA-256 digest of the exact request body.
- Signature comparisons use constant-time comparison.
- Requests have strict method/path/body-size validation.
- A timestamp freshness window limits replay opportunities.
- A durable SQLite nonce store rejects duplicate nonces per device, including after
  process restart.
- Pairing credentials must be resolved and scope-authorized by the caller before
  invoking this verifier; this module does not replace device pairing.

## Critical deployment boundaries

- HMAC authenticates and integrity-protects a request but does not encrypt it.
  Production transport must use HTTPS/TLS with certificate verification.
- The runtime must call the device pairing store to authenticate the credential and
  verify that the device has the required scope before accepting the request.
- Nonce storage must be durable and shared by all workers that accept requests for
  the same identity domain; otherwise cross-worker replay protection is incomplete.
- Rate limiting, TLS configuration, authorization middleware, event schema
  validation, and acknowledgement-after-commit must be implemented in the server
  integration phase.
- Do not expose an endpoint based on this primitive alone.

## Verification

```bash
python -m unittest tests.test_sync_transport_auth -v
python -m unittest discover -s tests -v
```

The focused tests cover valid signatures, invalid credentials, tampered payloads,
stale timestamps, path/body bounds, malformed signatures, and replay after reopening
the SQLite store.
