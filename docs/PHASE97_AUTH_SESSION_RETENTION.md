# Phase 97 — Authentication session retention

## Behavior

- Session resolution continues to reject revoked, expired, or disabled-user sessions regardless of cleanup.
- Auth storage initialization prunes expired session rows older than the 30-day retention window.
- `BuiltInAuthService.cleanup_sessions(retention_days=30)` provides explicit bounded maintenance for expired sessions and old revoked-session records.
- Retention is configurable per cleanup call within 1–365 days; invalid values fail closed with `ValueError`.
- Raw bearer tokens are not stored in the database; cleanup only removes stored token digests and metadata.

## Operational notes

The cleanup method is synchronous and should be called by a deliberate maintenance path, not on every request. The default local CLI uses short-lived in-memory session ownership and revokes on exit. Hosted deployments need their own scheduled maintenance policy, audit retention requirements, backup policy, and security review.

## Validation status

Regression tests cover removal of old expired/revoked sessions, preservation of recent valid sessions, retention-bound validation, and expiry enforcement before cleanup. This PR remains draft until CI runs on the current head and the dependency PRs are reviewed.
