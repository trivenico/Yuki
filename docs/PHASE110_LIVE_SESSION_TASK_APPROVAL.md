# Phase 110 — Live-session task approval binding

## Implemented

- `TaskRuntime` accepts a separately injected `verify_session(actor_id, session_id)` callback. The existing capability authorizer remains independent.
- `TaskRuntime.approve()` now requires the verifier to return the literal boolean `True` before it calls the durable approval store. A missing verifier, invalid session input, verifier exception, expired/revoked session, mismatched owner, or alternate session fails closed.
- `core.auth.task_session_verifier()` binds the supplied session identifier to a trusted current-token provider, resolves that token through `BuiltInAuthService.resolve_session()`, and checks the resolved account ID using constant-time comparisons.
- The local specialist-worker composition creates one cryptographically random process-scoped approval capability and injects a verifier that accepts only that exact capability for `LOCAL_OPERATOR_ACTOR`. It is generated before runtime construction and returned through the existing local runtime object for the interactive local operator surface.
- The specialist worker hashes that exact capability and supplies the digest to the store claim transaction. Protected tasks approved under another session remain queued and cannot be claimed by the new process after restart; the owner must explicitly approve them again in the current process.
- Tests now cover missing verifier, invalid session, verifier exception, exact token matching, same-owner alternate sessions, forged actor IDs, revocation, expiry, and local runtime approval binding.

## Security properties

- Authentication/session verification and capability authorization remain separate checks.
- Caller-supplied actor and session identifiers are not trusted without an independently injected verifier.
- The durable store continues to persist only a SHA-256 digest of the approval session identifier.
- A rejected approval does not update the workflow state or append an approval event.
- The local worker's random approval capability is process-scoped, not a replacement for OS account authentication or a hosted bearer-session implementation.

## Scope and limitations

- The hosted `BuiltInAuthService` verifier helper requires its caller to supply a trusted current-token provider. Do not build that provider from request JSON, task arguments, observed browser content, or other caller-controlled data.
- The local worker runtime currently uses a process-local capability because it is an explicitly local operator composition and does not own a `BuiltInAuthService` instance. Worker claims are constrained to approvals from that same process capability, so old approvals cannot silently execute after restart.
- Session verification happens immediately before the durable store transaction. The auth database and workflow database are separate stores, so this is not a cross-database atomic transaction. Revocation races remain bounded by the ordering of the live-session check and durable approval write; a stronger cross-store revocation fence would require a shared transaction/coordinator or revalidation at claim time.
- This change does not add an HTTP task approval endpoint and does not expand task execution permissions.
- No tests were run locally in this environment. CI must validate the pushed commit.
- No merge or deployment was performed.

## Next verification

1. Confirm all TaskRuntime construction sites inject an appropriate verifier.
2. Run `tests/test_task_runtime.py`, `tests/test_auth.py`, and `tests/test_local_worker_runtime.py` in CI.
3. Extend equivalent trusted-session verification and claim-time session scoping to any future hosted or multi-process task runtime.
4. Review the latest CI failures on the prior head before treating the branch as release-ready.
