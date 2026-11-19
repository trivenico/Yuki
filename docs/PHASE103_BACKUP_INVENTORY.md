# Phase 103 — explicit SQLite backup inventory

## Production web composition inventory

The web launcher in `scripts/run_web.py` currently constructs these SQLite stores under the configured workspace:

| Inventory key | File | Owner |
|---|---|---|
| `authentication` | `auth.sqlite3` | `BuiltInAuthService` |
| `inference_and_device_pairing` | `inference.sqlite3` | `DevicePairingService` and `InferenceJobService` share this database |
| `income_opportunities` | `income-opportunities.sqlite3` | `SQLiteIncomeOpportunityStore` |
| `conversations` | `conversations.sqlite3` | Included only when `storage_backend=sqlite` |

Provider selection is a JSON file, not SQLite. Device pairing and inference jobs are one database and must not be backed up twice. When conversation storage uses PostgreSQL, its backup must be handled by a PostgreSQL-native backup/restore process; it is not represented as a SQLite file.

## Inventory API

`core.backup_inventory.build_web_sqlite_inventory(config)` returns only the explicitly named databases above. It does not glob the workspace. A separately composed SQLite service (for example, a workforce task engine) must register its database explicitly through `additional_databases`; unknown databases are not silently included.

The inventory rejects missing files, symlinks, non-regular files, hard-linked files, duplicate names and duplicate resolved paths. The configured workspace must already exist. The helper inventories files only; it does not itself create backups, encrypt them, schedule jobs, or guarantee a cross-database transaction.

## Consistency and restore set limits

Each SQLite file must be backed up with `core.sqlite_backup.backup_sqlite_database`, which uses SQLite's online backup API and integrity check. A set of individual database snapshots is not an atomic cross-database snapshot. For coordinated multi-store consistency, quiesce writes or add an application-level maintenance barrier before capturing the set. The current web composition does not yet provide such a barrier.

Do not back up arbitrary workspace files, caches, temporary databases, model files, credentials, or sandbox staging directories by wildcard. Backups contain sensitive authentication and user data and must be written outside the live workspace to a separately provisioned protected destination.

## Encryption, retention and restore

Encryption and key management are intentionally not improvised in this phase. Use an established storage/provider encryption mechanism with separately managed keys and documented recovery access. Retention must preserve a last-known-good verified backup and must never delete files based on an untrusted path or filename.

Restore validation must occur in a fresh isolated workspace. It must verify SQLite integrity and application-level schema/startup invariants before any operator considers promotion. Never restore over a live production database during a test.

## Tests and verification

`tests/test_backup_inventory.py` covers exact inventory membership, exclusion of unrelated SQLite files, PostgreSQL conversation exclusion, explicit external database registration, missing files, symlinks, duplicate paths and invalid names. Tests have been committed but have not been independently executed in this environment. Check the current PR head's GitHub Actions results before treating them as passing.
