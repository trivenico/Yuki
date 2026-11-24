# Audit 071 — Database Security, Migrations, Backup Recovery & Disaster Recovery

**Scope:** SQLite/PostgreSQL persistence, transaction behavior, schema evolution, connection security, backup/restore, integrity checks and disaster recovery.

**Review basis:** source inspection on main of core/conversation_store.py, core/postgres_conversation_store.py, core/conversation_store_factory.py, core/oauth_credential_vault.py, core/approval_audit.py, core/sqlite_file_security.py, tests/test_conversation_store.py, tests/test_approval_audit.py, tests/test_sqlite_file_security.py, README.md, pyproject.toml, and requirements.txt. Conventional migration/backup scripts and documentation paths were checked and not found at those exact locations. Repository search for common migration and backup markers did not identify a framework or procedure. This is not proof that external deployment automation does not exist.

## Positive controls observed

- SQLite conversation store uses WAL mode, busy_timeout, explicit foreign-key enforcement per connection, parameterized SQL, and BEGIN IMMEDIATE for multi-step message writes.
- Message, conversation, generation-run and lease tables have primary/unique/check constraints and declared cascade relationships.
- PostgreSQL backend uses a bounded psycopg connection pool, validates connection URL scheme/host and pool bounds, and mirrors the conversation schema with foreign-key constraints.
- PostgreSQL multi-step operations use explicit transaction boundaries through the shared connection adapter; BEGIN IMMEDIATE is translated to BEGIN.
- SQLite paths are created as regular single-link files and secured to owner-only mode on POSIX; symlinks and hard links are rejected where supported.
- Tests cover owner scoping, optimistic concurrency, idempotency, generation lease fencing, audit chain tamper detection, and private SQLite file permissions.
- OAuth credential persistence uses transactional writes and host-provided authenticated encryption.

## Findings

### P1 — Schema evolution is initialization-by-CREATE, not a versioned migration lifecycle

The inspected SQLite and PostgreSQL conversation stores initialize tables with CREATE TABLE IF NOT EXISTS. The approval audit store contains an inline conditional ALTER TABLE migration for pending_id_hash and rebuilds the chain. No central schema version table, ordered migration registry, migration lock, preflight, rollback strategy, or migration test suite was identified in the reviewed paths.

**Impact:** future schema changes may be applied inconsistently across installations, and a failed or partially applied change may require manual recovery. The audit store's inline migration is a one-off example, not a general migration framework.

**Recommendation:** introduce explicit ordered migrations with immutable IDs/checksums, transactional application where supported, schema-version tracking, concurrency locking, backup/preflight requirements, and documented forward-fix/rollback policy. Test upgrades from every supported released schema and interrupted migration recovery.

### P1 — Backup, restore and disaster-recovery behavior is not demonstrated in repository code

No backup/restore scripts or runbook were found at the conventional paths checked, and searches did not identify sqlite3_backup, VACUUM INTO, pg_dump, or automated integrity-check usage. WAL mode means copying only the main SQLite database file while writes are active may omit committed data still present in the WAL. PostgreSQL recovery depends on external backup/PITR configuration not demonstrated by the application source.

**Impact:** the reviewed repository does not establish recoverable backups, tested restore points, RPO/RTO, or integrity validation after restoration.

**Recommendation:** use SQLite's online backup API or a coordinated checkpoint/quiesce process; use PostgreSQL base backups plus WAL archiving/PITR for production. Encrypt backups, isolate backup credentials, apply retention/immutability, and run scheduled restore drills that verify schema, row counts, application-level invariants and audit-chain integrity. Publish measured RPO/RTO, not aspirations.

### P1 — PostgreSQL transport security is not enforced by the store constructor

The PostgreSQL store validates URL scheme and hostname but accepts connection strings without requiring TLS or certificate verification. The code delegates TLS behavior to libpq defaults/connection parameters; it does not enforce sslmode=verify-full or an equivalent trusted CA policy.

**Impact:** deployments can configure PostgreSQL connections without verified server identity, depending on deployment defaults and network topology.

**Recommendation:** make production TLS verification an explicit configuration requirement (for example, sslmode=verify-full with trusted CA material), reject insecure production configurations, and test hostname/certificate failures. Keep local development overrides explicit and separate.

### P2 — SQLite and PostgreSQL operational durability settings are not standardized

SQLite stores enable WAL but do not explicitly set/document synchronous, checkpoint policy, filesystem assumptions, or WAL monitoring. PostgreSQL durability, replication, connection TLS, backup/PITR and failover settings are deployment-owned and not expressed in the store contract.

**Recommendation:** publish supported durability profiles, define disk-full and lock-contention behavior, monitor WAL/pool growth, and validate crash recovery under realistic write load. Document which guarantees depend on filesystem, host, database server, and cloud provider.

### P2 — Integrity validation is test-oriented rather than an operational health gate

The inspected tests exercise logical consistency and audit-chain verification, but the stores do not expose a standard startup/maintenance health procedure for SQLite PRAGMA integrity_check / foreign_key_check or PostgreSQL consistency/backup verification. No scheduled integrity scan or post-restore validation workflow was found in the reviewed paths.

**Recommendation:** add operator-invoked and scheduled read-only integrity checks, report results to monitoring, and define fail-closed behavior for corrupted security-critical stores. Avoid automatic repair that overwrites evidence before preserving a forensic copy.

## Remediation decision

No runtime patch was submitted. These gaps require coordinated choices about supported schema versions, production TLS policy, database operators, backup infrastructure, and recovery objectives. A local code change cannot safely claim backup or disaster-recovery readiness without infrastructure and restore-drill evidence.

Recommended implementation sequence:
1. establish schema versioning and migration framework for SQLite and PostgreSQL;
2. enforce verified TLS for production PostgreSQL;
3. implement SQLite online backup and PostgreSQL backup/PITR runbooks;
4. add integrity/foreign-key checks and post-restore verification;
5. run automated migration/restore drills and publish measured RPO/RTO.

## Validation status

- Source and selected regression-test inspection completed for the paths listed above.
- Tests were read but NOT EXECUTED.
- No PostgreSQL server, TLS certificate, production filesystem, backup artifact, restore drill, failover, or crash-recovery test was available/performed.
- No runtime code was changed in this audit.
