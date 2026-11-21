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
- POSIX restores apply owner-only file permissions (`0600`) and directory permissions (`0700`); Windows uses the destination user's inherited ACLs.
- The source data is never modified by backup creation.

Install `requirements-memory-security.txt` (or the desktop extra) to provide
the cryptography package. This is a portable passphrase-encrypted archive; it
does not depend on the source computer's OS keyring.

The native desktop client now exposes a Backup & Restore tab. Backup creation asks for a passphrase twice and runs off the UI thread. Restore requires a selected backup, passphrase, and a user-selected destination folder; the active Yuki profile and its parent directories are rejected, and the destination must be empty. The passphrase fields are cleared before work begins and are not logged or persisted.
Remaining UI work: add passphrase strength guidance, cancellable progress reporting, and an end-to-end clean-device restore walkthrough. The current worker prevents window close/sign-out while an operation is running. Do not log passphrases or archive contents. Production qualification still requires large-dataset, interrupted-write, and clean-device restore tests. Restore currently writes to a separate folder and does not activate or hot-swap the restored profile.


The desktop profile backup includes the private desktop data root and the
entire configured workspace tree under `workspace/`. SQLite databases in
both roots are snapshotted using SQLite's backup API and checked during
restore. Symlinks, special files, orphan SQLite sidecars, path collisions, and
files exceeding the global archive limits are rejected. Restore writes a
separate YUKI_HOME-compatible package and does not activate or overwrite the
running profile.

## Restored profile package layout

New backups use manifest schema version 2 and restore as a `YUKI_HOME` package:
- `.yuki/` contains the desktop authentication database, task database, encrypted memory, and other files from the private desktop data root.
- `workspace/` contains the configured workspace files, including the canonical conversation database.

Choose an empty parent folder as the restore destination. To use the restored profile, configure `YUKI_HOME` to that parent folder and configure the workspace to its `workspace/` child. Yuki will then resolve its desktop profile as `YUKI_HOME/.yuki`. Restore does not change the running configuration or activate the profile automatically.

Schema version 1 backups remain readable. Their root-level desktop files are normalized into `.yuki/` during restore, while existing `workspace/` entries retain their location.


## Portable encrypted-memory option

The backup UI offers an opt-in checkbox to include encrypted memory for the
currently authenticated account. If account-scoped memory exists and this
option is not selected, backup creation is refused rather than silently
producing an incomplete restore. Legacy profile-wide `memory.enc.json` is
quarantined and is never exported or assigned to an account.

Account-scoped key metadata uses envelope schema version 2. It contains the
profile UUID, a domain-separated digest of the profile UUID and authenticated
actor ID, and the memory data-encryption key. The raw actor ID is never written
to the archive. The key metadata is wrapped with AES-256-GCM using a key derived
from the backup passphrase; the authenticated associated data includes the
profile UUID and opaque account namespace. The wrapped value is included only
inside the encrypted archive. The raw memory key is never written to the
temporary plaintext tar archive or a separate file.

On restore, the user must explicitly enable memory-key import and be signed in
as the same authenticated account that created the backup. Yuki recomputes the
account namespace from the restored profile UUID and the live authenticated
actor ID; a mismatch fails closed before the profile is published. The key is
installed under the account-specific OS-keyring label, and the matching
account-scoped ciphertext filename is validated before key installation.
Keyring collisions with a different existing key fail closed. If filesystem
publication fails after a new key is installed, Yuki attempts to remove that
newly created keyring entry.

Legacy profile-only portable key envelopes remain readable only through the
legacy import path. They cannot unlock account-scoped memory and are not
accepted by the authenticated desktop restore path. Backups created without
the portable-memory option do not include the memory key and cannot unlock
memory on another computer or OS account. Keep the backup passphrase safe: it
protects both profile data and the optional portable memory key.

## Account-scoped memory identity

Each desktop profile has a stable, non-secret UUID in
`.yuki/profile_identity.json`. New desktop memory stores use a domain-separated
digest of that UUID and the authenticated actor ID to derive both an opaque
ciphertext filename and an OS-keyring label. This prevents two authenticated
accounts sharing one desktop profile from sharing memory ciphertext or keys.
The raw actor ID is never embedded in filenames, keyring labels, or portable
backup metadata. The previous profile-wide `memory.enc.json` and its key are
left untouched and inaccessible; no automatic migration or first-user
assignment is performed.
