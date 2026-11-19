# Phase 106 — isolated SQLite restore verification

## API

`core.sqlite_restore_verify.restore_and_verify_sqlite_backup_set(...)` first invokes the Phase 105 read-only backup verifier, then restores each expected database into a newly created private staging directory under a separately provisioned restore root.

The restore root must already exist, be outside the live workspace and source backup set, and on POSIX be owned by the service user with no group/other permissions. The function never accepts a production database destination path. A successful restore is atomically published as a new `restore-<uuid>` directory; it never overwrites an existing directory.

## Validation contract

The caller must provide a trusted `application_validator` callback. It receives a mapping of logical database names to restored staging paths and must:
- open restored databases read-only;
- perform application-specific schema and startup-invariant checks;
- return exactly `True` only when all checks pass.

A missing validator, exception, or non-True result fails closed and removes the private staging directory. After the callback returns, the restore coordinator verifies that the directory contains only the expected database files, rejects symlinks and hardlinks, and compares SHA-256 digests to ensure the callback did not modify the restored database files. Only then does it write a private restore report and atomically publish the restore directory.

The report records the restore ID, source backup-set ID, verification timestamp, SQLite integrity status, application validation status, and restored logical database names/sizes. It does not include absolute paths or database contents.

## Safety boundary

This is an isolated restore-verification primitive, not a production cutover mechanism. It does not stop production writes, change application configuration, promote a restore, replace live databases, run migrations, or connect to production services. Restore roots must be provisioned on protected storage and should be disposable test locations.

The application validator is trusted code and must not use the supplied paths for writes or external side effects. The coordinator detects database mutation and unexpected staging files after the callback, but it is not a sandbox for untrusted validator code.

## Tests and verification

`tests/test_sqlite_restore_verify.py` covers successful isolated restore/report creation, validator rejection and exception cleanup, live-workspace destination rejection, mandatory application validation, and detection of validator mutation. Tests are committed but have not been independently executed in this environment. Confirm a successful CI run before treating them as passing.
