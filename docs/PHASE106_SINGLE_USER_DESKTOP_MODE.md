# Phase 106 — single-user local desktop mode

## Product decision

Yuki Desktop is a personal application operated by one local OS user. The desktop no longer presents registration, username/password, sign-in, sign-out, or account-switching UI. Launch opens the personal workspace directly.

## Implementation

- Added `BuiltInAuthService.open_local_profile_session()`: on first launch it creates one local administrator with a cryptographically random password that is never displayed or persisted; it then issues an opaque, expiring session token.
- Existing installations with exactly one enabled account reuse that account ID, preserving owner-scoped conversation, task and knowledge records.
- Installations with multiple accounts fail closed with a migration-required error rather than silently choosing an owner or merging private data.
- Added `DesktopSessionManager.open_local_profile_session()`: rotates any previous token and stores the new opaque token in the operating-system credential store.
- Desktop startup now opens the local profile automatically. The sign-in form and environment-based bootstrap credentials are removed.
- The header identifies the personal workspace and offers safe application close rather than sign-out.
- Shutdown revokes the controller session and the OS-keyring-backed session token before closing services.
- Expired local sessions are cleaned up and reopened automatically when cleanup succeeds.

## Security boundaries retained

- This is not a hosted/API authentication bypass. The local-session issuance method is for the trusted desktop process only and must never be exposed through HTTP, plugins or remote tools.
- Local profile access relies on the operating-system account boundary and protected local storage / OS credential store.
- A database containing multiple enabled accounts is rejected. No account is silently selected, merged or deleted.
- Task approvals, destructive actions, external communications, credentials and computer-use permissions remain separately gated.
- Microphone and screen observation remain off until explicitly enabled.
- A local OS lock / biometric gate can be added later without restoring an application-level username/password form.

## Migration and verification requirements

- Existing one-account installations preserve the current owner ID.
- Multi-account installations require a reviewed migration to a single owner before this mode can be enabled.
- Verify first launch, restart, token rotation, expiry recovery, shutdown revocation, existing one-account continuity and multi-account fail-closed behavior.
- Run focused auth/session tests, desktop Qt smoke tests and full CI before release.
- Tests have been added but have not been executed in this environment.
