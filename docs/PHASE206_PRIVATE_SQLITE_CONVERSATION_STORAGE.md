# Phase 206 — private SQLite conversation storage

## Goal

Protect locally persisted conversation text and metadata from access by other
OS users on POSIX systems. SQLite file permissions are defense in depth; this
phase does not provide encryption at rest or protect against another process
running as the same OS account.

## Implementation

- Add `core.sqlite_file_security.prepare_private_sqlite_path` as the shared
  path-preparation primitive for SQLite stores that contain personal data.
- Resolve the parent directory, create missing parent directories, and open
  the final path with `O_NOFOLLOW` where supported.
- Require a regular file with a single hard link; reject symlinks and hard-linked
  database files rather than changing permissions on an aliased target.
- On POSIX, set the database file mode to `0600` for new and pre-existing files.
- Apply the helper to `ConversationStore` and `BuiltInAuthService`.
- Apply it to `DevicePairingService` and `InferenceJobService`, which intentionally
  share one SQLite database for pairing credentials and inference jobs.
- Apply it to `SQLiteOAuthCredentialVault`, `SQLiteMCPToolPolicyStore`,
  `WorkflowRunStore`, `SQLiteApprovalAuditLog` and
  `SQLitePendingApprovalStore` and `SQLiteIncomeOpportunityStore`.
- WAL and shared-memory sidecars are checked by tests when present; actual
  sidecar permission behavior remains platform/SQLite dependent.

## Security boundaries and limitations

- This phase now hardens conversation, authentication, paired-device, inference,
  encrypted OAuth credential, MCP policy, workflow, approval-audit and pending-
  approval and income-opportunity SQLite databases. Any additional feature-specific
  stores not listed above still require separate inventory and adoption.
- POSIX mode `0600` is not encryption and does not protect data from the same
  OS user, root, malware running as the owner, or an untrusted backup process.
- Windows ACL enforcement is not implemented here. The Windows user profile
  and deployment installer must provide appropriate ACLs; this phase makes no
  claim that POSIX mode bits provide equivalent Windows protection.
- Parent directories that already exist are not chmodded because they may be
  shared application or repository directories. New leaf directories are
  created with owner-only mode where the platform honors POSIX modes.
- Backup archives, copied databases, WAL checkpoints, retention, deletion,
  key management and restore drills remain separate acceptance requirements.
- This does not resolve the older authentication-database follow-up issue
  (#269); that issue needs current-path reconciliation before implementation.

## Tests

- New database mode and tightening an existing permissive database.
- Symlink and hard-link rejection.
- Reject a directory as a database file.
- Verify conversation database mode and WAL/SHM sidecar modes when present.
- Verify pre-existing permissive authentication DBs are tightened, the shared
  device-pairing/inference DB is protected, and the OAuth, MCP policy, workflow,
  approval audit, pending approval and income opportunity stores are secured.
- POSIX-only permission assertions are skipped on platforms without equivalent
  mode semantics.

Exact-head cross-platform CI is required. No local test execution or production
security certification is claimed.
