# Phase 167 — Owner-bound OAuth 2.1 PKCE transactions

## Purpose

Establish a narrowly scoped, single-use authorization transaction primitive for future provider account linking. It creates OAuth authorization URLs with PKCE S256 and binds callback state to the authenticated Yuki owner, provider, issuer, client ID, redirect URI, requested scopes, and a short expiry.

## Security contract

- Generate 256-bit random state and PKCE verifier; use SHA-256 S256 challenge.
- Persist only a SHA-256 state digest. Encrypt the verifier through an injected application vault with owner/provider/state-bound associated data.
- Never accept a verifier, state, token, client secret, or vault key from model-generated skill arguments.
- Callback consumption is transactional and single-use. Owner, provider, issuer, and expiry must match.
- A mismatch invalidates the pending transaction; callbacks cannot be replayed.
- Require HTTPS URLs, default port, no userinfo/fragments, and a bounded redirect URI. The caller must register the exact redirect URI with the provider.
- Require an explicit authorization-endpoint hostname allow-list; arbitrary HTTPS endpoints are rejected.
- Bound outstanding transactions to 32 per owner and expire them after at most 30 minutes (10 minutes by default).
- Restrict the SQLite database file to owner-only permissions on POSIX systems; Windows deployments must supply equivalent filesystem ACLs.
- Requested scopes are explicit, bounded, unique and printable ASCII.

## Deliberate limitations

This phase does not implement an HTTP callback route, OAuth token exchange, dynamic client registration, token storage, refresh, revocation, or Upwork account integration. The encrypt/decrypt provider must be backed by a production KMS/OS vault; tests use a test-only reversible vault and do not represent production encryption. The authorization host allow-list and exact redirect URI must come from trusted, reviewed provider configuration. Do not enable an Upwork connector until its app registration, approved redirect URI, scopes, and terms have been verified.

## Tests

Tests cover S256 construction, endpoint allow-list enforcement, owner/provider/issuer binding, expiry, one-time consumption, plaintext secret-at-rest exclusion, vault failures, bounded pending transactions, and reserved parameter override rejection.
