# Login Account-State Timing Enumeration Audit

## Scope

Source review of `core/auth.py`, `api/conversations.py`, `tests/test_auth.py`, and the authentication contract in `docs/PHASE81_BUILT_IN_AUTH.md` at PR #1346 head `0790fb7352b8ed734b4355c81ad9707d972c4539`.

## Finding: login work factor differs by account state

**Severity: P3 — account enumeration side channel**

`BuiltInAuthService.authenticate` returns the same generic `AuthenticationError` for invalid credentials, and the HTTP login endpoint maps that to the same `401 invalid_credentials` response. However, its internal work differs:

- Unknown username: performs a dummy PBKDF2-HMAC-SHA256 derivation using the configured iteration count.
- Existing disabled account: sets the invalid flag without performing a password derivation.
- Existing account in active lockout: sets the invalid flag without performing a password derivation.
- Existing unlocked account: performs PBKDF2 using the stored account parameters.

This creates a potentially measurable timing distinction between nonexistent usernames and existing disabled or locked accounts. Generic error text and status do not eliminate a timing side channel.

The configured default password work factor is 600,000 PBKDF2 iterations, so the difference between a full derivation and the short-circuit paths may be observable under sufficiently controlled conditions. Network jitter and deployment rate limiting affect practical exploitability.

## Impact

An unauthenticated caller may be able to infer whether a username exists and, in some cases, whether its account is disabled or locked. This is account metadata disclosure, not an authentication bypass or password compromise.

## Recommended remediation

1. Perform a PBKDF2 derivation for every syntactically acceptable login attempt, including disabled and locked accounts.
2. Use a dummy salt and the current configured iteration count for accounts whose stored password parameters should not be consulted.
3. For existing unlocked accounts, still compare the derived value with the stored hash using constant-time comparison; for disabled or locked accounts, discard the derivation result and return the same generic failure.
4. Ensure all paths use a consistent work factor. If per-account iteration counts are retained for migration, define a bounded policy to avoid unbounded timing variation.
5. Add unit tests asserting the password-derivation path is exercised for unknown, disabled, locked, and ordinary existing accounts.
6. Add a controlled timing regression test only as supplemental evidence; avoid relying on noisy wall-clock thresholds in ordinary CI.
7. Apply edge rate limiting to login attempts and monitor repeated username probes.

## Password reset and account recovery coverage

The inspected built-in auth contract explicitly states that password reset, MFA, email verification, and account recovery are not implemented in this phase. The API routes inspected expose login, logout, and current-user identity, but no password-reset or recovery endpoint was found in the repository tree at this PR head.

This is a documented product capability gap, not an exploitable reset-token defect: there is no reset-token lifecycle to assess for entropy, expiry, single-use enforcement, or concurrent redemption. If hosted users are expected to recover accounts without operator intervention, a separately designed recovery flow is required.

## Verification limitations

Source-only review. No runtime timing measurements, tests, CI workflows, or deployed ingress configuration were executed or verified. The timing side channel is inferred from the distinct code paths; practical remote distinguishability remains deployment-dependent.
