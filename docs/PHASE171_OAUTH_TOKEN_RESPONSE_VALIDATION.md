# Phase 171 — OAuth token response validation

## Objective

Parse authorization-server token responses into a bounded, typed grant before any credential persistence or use.

## Implemented

- Requires an object response with only supported token response fields.
- Validates opaque access and optional refresh-token syntax and size.
- Accepts Bearer token type only; unsupported token types fail closed.
- Validates positive integer expiry within a one-year maximum.
- Applies RFC 6749 scope behavior: omitted scope means the requested scope; returned scope may only narrow the requested set.
- Rejects malformed, duplicate, or widened scopes.
- Carries the issuer and resource context already validated by the caller into the grant object.
- Hides access and refresh token values from the grant's default representation to reduce accidental log disclosure.
- Adds tests for valid responses, optional refresh tokens, scope widening, malformed tokens, unsupported token types, expiry bounds, and sensitive repr behavior.

## Critical trust boundary

This is response-schema validation, not cryptographic token validation.

- The issuer and resource values are trusted context supplied by the caller; this module does not prove token audience, issuer claims, client binding, or subject identity.
- Opaque access tokens are not decoded or treated as JWTs. JWT validation requires a separate, standards-compliant signature/JWKS/issuer/audience/expiry implementation when the provider actually issues JWTs.
- This module does not persist credentials, refresh tokens, or identity claims.
- Never log, display, place in agent prompts, or expose the returned token strings to untrusted tools.
- Long-lived storage must use authenticated encryption with host-managed keys, atomic refresh-token rotation, revocation, and account disconnect.
- Unsupported response extensions, including ID tokens, are rejected rather than silently accepted or persisted.

## Validation

CI is authoritative for this stacked PR. No local test execution is claimed.
