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

The backup UI offers an opt-in checkbox to include the profile's memory key. When selected, key metadata is first wrapped with AES-256-GCM using a key derived from the backup passphrase and bound to the profile UUID with authenticated associated data. The wrapped value is then included in the archive, which is itself encrypted with the backup passphrase and memory-hard KDF. The raw data-encryption key is never written to the temporary plaintext tar archive, a separate file, or the restore staging directory.

On restore, the owner must enable the memory-key import checkbox. Yuki validates the backup manifest, profile identity, key metadata, and AES-GCM authentication of the restored memory ciphertext before installing the key into the destination OS keyring. Keyring collisions with a different existing key fail closed. If filesystem publication fails after a new key is installed, Yuki attempts to remove that newly created keyring entry.

Backups created without the portable-memory option remain restorable. Their memory remains dependent on the original OS keyring key; on a different computer or OS account, startup will correctly stop rather than replace or discard inaccessible memory. Keep the backup passphrase safe: it protects both profile data and the optional portable memory key.

## Profile-scoped memory keys

Each desktop profile now has a stable, non-secret UUID in `.yuki/profile_identity.json`. Its encrypted-memory key is stored in the OS credential store under a profile-specific account derived from that UUID. Existing installations migrate the original `default` key into the profile-specific account without rewriting ciphertext; the legacy key is retained so other older profiles are not broken. The profile identity is included in the encrypted backup. New profiles receive independent keys. Existing installations migrate the original shared key without re-encrypting memory, so their original key material is preserved. The optional encrypted-backup export/import path described above enables cross-device restore when selected.


## Coding-task execution backup gate

The authenticated desktop backup path holds the active profile's shared
`MaintenanceFence` for the full backup operation. Before snapshotting, it
checks the canonical coding-task plan/evidence database pair. Backup is refused
if the pair is incomplete, either database fails integrity/schema validation,
a task remains in `executing`, or evidence does not point to a completed task.
Restore performs the same check against the isolated staging directory before
publishing it.

This gate prevents a backup from silently preserving an unresolved coding
execution as if it were safely retryable. It does not reconcile that execution:
the owner must inspect independent operational evidence before taking any
manual disposition.

## Workspace conversation consistency

The native desktop conversation store now receives the same profile-root
`MaintenanceFence` used by the authenticated desktop backup operation. Its
SQLite connection holds that lease through the operation, so desktop chat
writes cannot race the backup snapshot of `workspace/conversations.sqlite3`.
This closes the first-party desktop conversation-writer gap. It does not
serialize arbitrary external editors or other processes that do not use the
Yuki fence, and it does not make multiple SQLite databases one transaction.
Do not describe the archive as a globally atomic snapshot of an externally
mutable workspace.

## Encrypted memory snapshot consistency

The native desktop encrypted-memory store now uses the same profile
`MaintenanceFence` as the backup controller. Memory reads and writes and the
optional portable key export participate in that lock. When
`include_memory_key=True`, the controller exports the key only after
acquiring the fence and keeps the lease through archive creation. This avoids
a first-party memory write racing the archived ciphertext or key envelope.

This guarantee applies to the native desktop encrypted-memory composition.
Direct external edits to `memory.enc.json`, unrelated processes, and OS
keyring changes do not participate in Yuki's fence.

## Restore publication and durability failure semantics

Restore validates and fsyncs the isolated staging tree before publication.
When portable memory import is requested, the key is installed in the OS
keyring only after the staged ciphertext has been authenticated against it.
If directory publication then fails, restore attempts to remove only a key
entry newly created by that invocation. A pre-existing matching key is never
removed; a conflicting key is rejected.

Restore now creates an owner-only sidecar receipt in the destination
parent after staging validation and before importing a portable memory key or
publishing the profile. The receipt is fsynced before publication. Successful
publication is followed by a destination-parent fsync and a COMMITTED receipt
update. The restore result includes the operation ID and receipt path.

If the destination rename succeeds but syncing the destination parent fails,
the receipt remains PREPARED. Use
`ui.desktop.restore_receipts.inspect_restore_receipt(receipt_path,
expected_parent=..., operation_id=...)` to inspect it without modifying the
destination, receipt, or keyring. A PREPARED receipt with an existing
destination is reported as PUBLISHED_UNCONFIRMED. Preserve the destination and
imported key; do not retry or overwrite it. A PREPARED receipt without a
destination is reported as NOT_CURRENTLY_PUBLISHED, but retain it for review:
a missing destination does not prove every preceding operation was rolled
back. A failed publication with successful key rollback is recorded as
NOT_PUBLISHED. Key-import or rollback ambiguity is recorded as
RECOVERY_REQUIRED where the receipt can be updated.

Receipt directories must be owned by the current user and not group/world
writable. On non-POSIX platforms receipt creation currently fails closed
because a separately verified directory-flush strategy is not implemented.
Even on POSIX, durability depends on the filesystem and storage device honoring
fsync. If receipt persistence itself fails during keyring recovery, inspect
both the receipt and OS keyring; never infer that the key is absent. The
receipt is an operator recovery aid, not a globally atomic transaction across
the filesystem and OS keyring.
