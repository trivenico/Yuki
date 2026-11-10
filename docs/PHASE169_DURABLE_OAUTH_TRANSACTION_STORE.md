# Phase 169 — Durable OAuth transaction store

## Objective

Persist OAuth authorization transactions safely across web-process restarts while binding every callback to the initiating authenticated Yuki owner and browser session.

## Implemented

- SQLite-backed transaction records survive process restarts.
- State, owner ID, and browser session ID are stored only as keyed HMAC digests.
- PKCE verifier is encrypted before persistence using a required injected trusted-host encryption callback.
- Encryption and HMAC keys are separate; the HMAC key must be at least 256 bits and stable across restarts.
- Strict transaction TTL bounds (60 seconds to at most 30 minutes; default 10 minutes).
- Atomic BEGIN IMMEDIATE state consumption; concurrent callbacks can have only one winner.
- Owner and session mismatch are rejected without consuming a legitimate transaction.
- Expired transactions are rejected and marked consumed.
- Decryption failure consumes the transaction and fails closed, preventing replay after a partial/ambiguous callback.
- Expired record cleanup supports bounded retention.

## Required integration constraints

This store is not a complete OAuth callback service.

- Host must inject authenticated encryption with integrity (e.g. an AEAD-backed secret manager); do not use the test cipher from unit tests.
- HMAC key must be provisioned and rotated through trusted deployment secrets, never source code or agent context.
- The host must create the transaction only after authenticating the owner and binding it to the actual browser session.
- Callback handler must validate exact redirect URI, issuer and state using Phase 167/168 helpers before consuming the transaction.
- Callback handler must consume state exactly once, then exchange code using the returned verifier and validated issuer/resource.
- Transaction storage must live in a private workspace directory with appropriate OS permissions, backup protection, and database access controls.
- No arbitrary remote HTTP requests are made here; DNS/IP safety, redirect policy, response size limits, and connection pinning remain mandatory for metadata/token transport.
- Token exchange, token validation, encrypted long-lived token storage, refresh rotation, revocation, disconnect, and account linking are not implemented.
- Upwork remains disabled until the complete OAuth lifecycle and exact callback URI approval are in place.

## Validation

The new tests cover persistence, plaintext-secret exclusion, owner/session binding, atomic single-use semantics under concurrent callers, expiry, duplicate state, encryption/decryption failures, cleanup, and key/cipher requirements. CI on the PR is authoritative; no local execution is claimed.
