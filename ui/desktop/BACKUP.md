# Yuki Desktop encrypted backup and restore

The backup API in `ui.desktop.backup` creates a portable authenticated backup
using a user-supplied passphrase. It derives a 256-bit key with scrypt and
encrypts the archive with AES-256-GCM. The passphrase is never written to the
backup or stored by this module. Keep it safe: losing it makes the backup
unrecoverable.

## Current limits

- Maximum uncompressed source/archive size: 512 MiB.
- Maximum number of files: 10,000.
- Backup source must not contain symbolic links or special files.
- Backup output must be outside the source directory and must not already exist.
- Restore destination must be empty; existing data is never overwritten.
- SQLite databases are snapshotted with SQLite's backup API and integrity checked.
- All restored files are checked against the authenticated manifest.
- The source data is never modified by backup creation.

Install `requirements-memory-security.txt` (or the desktop extra) to provide
the cryptography package. This is a portable passphrase-encrypted archive; it
does not depend on the source computer's OS keyring.

The first implementation is a tested service module, not yet a desktop wizard.
Before exposing it in the UI, add passphrase confirmation, strength guidance,
confirmation before restore, cancellation/progress reporting, and a warning
that the passphrase cannot be recovered. Do not log passphrases or archive
contents. Production qualification still requires large-dataset, interrupted
write, and clean-device restore tests.
