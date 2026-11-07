# Phase 296 — Host-issued reviewer sessions

## Implemented

Adds a host-side reviewer session authority with signed, short-lived, artifact-scoped bearer tokens.

- Session claims bind reviewer identity, artifact SHA-256, allowed action set, issue time, expiry, version, and random session ID.
- HMAC-SHA256 authenticates the canonical token payload.
- TTL is bounded to 30–3600 seconds (default 15 minutes).
- Authorization is checked both when issuing and consuming a session.
- SQLite records support explicit revocation and one-time operation IDs per action.
- Replay attempts are rejected by a uniqueness constraint inside an immediate transaction.
- Session secret is injected by the host and never persisted by this module.

## Integration boundary

This authority is a standalone primitive. It is not yet wired into ReviewerTaskLedger or the default local runtime. The host must deliver tokens over a trusted worker transport, keep tokens out of prompts/logs, and provide the secret from protected storage. The database must be protected against rollback if revocation and replay guarantees are expected across restores.

## Tests

Five tests cover artifact/reviewer binding, replay rejection, token tampering, revocation, and authorization re-check. Tests are authored but not executed here.
