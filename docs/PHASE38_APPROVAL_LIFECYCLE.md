# Phase 38 — Payload-bound one-time approvals

## Audit finding

The existing approval store was an in-memory token map with a mutable argument snapshot. It did not bind decisions to action risk, a canonical payload digest, or an expiry. The subprocess dispatcher accepted `approved=True`, which is a caller-supplied boolean rather than evidence of a recorded approval. A token could not reliably prove that the exact invocation had been reviewed.

## Changes

- Approval records bind the skill/action identifier, risk level, canonical JSON SHA-256 digest, creation time, and expiry.
- Canonical serialization sorts object keys, rejects NaN/infinity and non-JSON values, and produces a detached JSON snapshot.
- Approval consumption is atomic under a lock and one-time. A mismatched action, risk, payload, expired token, or replay fails closed; mismatch consumes the token.
- Returned approval objects do not share the internal mutable argument object.
- The subprocess dispatcher now creates a reviewable approval request and requires its one-time `approval_id` at execution; the old `approved=True` bypass is removed.
- Execution receives the store's approved argument snapshot, not the caller's mutable argument object.
- Added regression tests for payload/action/risk mismatch, expiry, replay, mutation, and non-JSON inputs.

## Important limitations

- The store is still in-memory. Pending approvals are lost on restart, which is fail-closed but not durable.
- This is not a cryptographic identity/signature system; the token is an opaque bearer credential and must be protected by the UI/API.
- `SubprocessExecutor` is a bounded subprocess runner, not a complete OS sandbox. Child processes inherit the current user's privileges and may access resources available to that user. Do not run untrusted generated code until a container/OS sandbox and explicit capability enforcement are in place.
- The legacy in-process `YukiCoreAgent.run_skill()` path still invokes skill objects in-process after approval. Full migration to one facade remains outstanding.
- Approval should be displayed with the exact action, risk, arguments, and expiry; the API/UI layer must not allow clients to approve a different payload.

## Verification

The changes and tests are committed on `phase6/production-hardening`. Latest-head CI must pass before calling the phase verified. This document does not claim production certification.

## Next phase

Phase 39 should migrate all legacy skill invocation to the single dispatcher/facade, add persistent approval storage with transaction-safe consumption and identity/session binding, and introduce OS-level isolation for untrusted plugin execution.
