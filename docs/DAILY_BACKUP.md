# Daily backup scripts

These scripts create timestamped archives of Yuki's configured local workspace. Before archiving, they call `scripts/sqlite_workspace_snapshot.py` to create a staging copy. Every recognized SQLite database is copied using SQLite's online backup API and checked with `PRAGMA integrity_check`; committed WAL content is included, and live `-wal`, `-shm`, and rollback-journal sidecars are not copied as loose files.

Non-database files are copied normally and are not captured in one atomic application-wide transaction. **Stop Yuki before backing up** when you need a consistent snapshot across SQLite databases and ordinary files.

The scripts do not upload data. By default, archives remain plaintext for backwards compatibility. Set `YUKI_BACKUP_KEY_FILE` to opt in to authenticated AES-256-GCM encryption; encrypted archives use the `.yukibak` extension.

## Linux / macOS

From the repository root:

```sh
sh scripts/daily_backup.sh
```

Source precedence:

1. First positional argument.
2. `YUKI_BACKUP_SOURCE` (explicit source override).
3. `YUKI_WORKSPACE` (workspace override; relative paths resolve under `YUKI_HOME` when set, otherwise repository root).
4. The default `yuki_workspace` beneath `YUKI_HOME` when set, otherwise beneath the repository root.

The script does not parse a custom YAML config file. If your configured workspace comes from a custom config, set `YUKI_BACKUP_SOURCE` or pass the source path explicitly.

Destination and retention:
- Destination: `YUKI_BACKUP_DIR` if set, otherwise `./BACKUP/YukiData`
- Retention: `YUKI_BACKUP_RETENTION_DAYS`, default 14 days (1–3650)
- Python executable: `YUKI_PYTHON` if set, otherwise `python3` or `python` from PATH
- Encryption: set `YUKI_BACKUP_KEY_FILE` to a protected 32-byte key file stored outside the backup directory

Override source and destination with positional arguments:

```sh
sh scripts/daily_backup.sh "/media/my-ssd/YukiData" "/media/backup-drive/YukiBackups"
```

The script requires Python, SQLite support in that Python build, and standard `tar`, `find`, and shell utilities. For encrypted backups, install `requirements.txt` so `cryptography` is available. It validates the TAR before encryption/publication and deletes only matching Yuki archive files older than the configured retention period.

## Windows

Double-click `scripts/daily_backup.bat` or run it from Command Prompt:

```bat
scripts\daily_backup.bat
```

You can pass source and destination directories:

```bat
scripts\daily_backup.bat "E:\YukiData" "F:\YukiBackups"
```

The same source precedence, `YUKI_BACKUP_DIR`, and `YUKI_BACKUP_RETENTION_DAYS` rules apply. The script uses `YUKI_PYTHON` or `python` from PATH, plus the Windows-provided .NET ZIP APIs through PowerShell. Hidden files are included and every archive entry is read before publishing the archive. Set `YUKI_BACKUP_KEY_FILE` to a protected 32-byte key file outside the backup directory to publish an encrypted `.yukibak` instead of a plaintext `.zip`. For a custom YAML workspace, use `YUKI_BACKUP_SOURCE` or a positional source argument.

## Schedule it daily

Scheduling is intentionally **not automatic**.

- Linux/macOS: use your user crontab or OS scheduler to run the script once daily. Use absolute paths and log output to a private file. Example cron entry (edit paths for your machine):

  ```cron
  0 3 * * * /bin/sh "/absolute/path/to/Yuki/scripts/daily_backup.sh" >> "$HOME/yuki-backup.log" 2>&1
  ```

- Windows: create a Task Scheduler task that runs `cmd.exe` with arguments `/c "C:\absolute\path\to\Yuki\scripts\daily_backup.bat"`. Set the correct working directory and choose a time when the SSD is connected and unlocked.

Neither scheduler can back up an SSD that is disconnected. Test a manual backup and restore before scheduling unattended runs.


## Restore into an isolated directory

Use the restore utility to validate and extract an existing ZIP or gzip TAR backup into a **new directory**. It does not overwrite an existing destination.

```sh
python scripts/restore_workspace.py "/path/to/yuki-data-YYYYMMDD_HHMMSS.tar.gz" "/path/to/restore-test"
```

On Windows, use the ZIP produced by `daily_backup.bat`:

```bat
python scripts\restore_workspace.py "F:\YukiBackups\yuki-data-YYYYMMDD_HHMMSS.zip" "E:\YukiRestoreTest"
```

The utility stages extraction beside the destination, rejects path traversal, absolute/drive paths, duplicate/case-colliding names, links, special files, SQLite journal sidecars, oversized archives and unsupported formats, then runs SQLite integrity checks before publishing the restored workspace. On POSIX it applies owner-only permissions (directories 0700, files 0600). Restore into a fresh location, inspect the result, and only then use it as a Yuki workspace. Do not restore over a running workspace.

The restore utility accepts plaintext ZIP/TAR archives only. For an encrypted archive, authenticate and decrypt it to a private temporary file first, then pass that file to the restore utility. It does not merge data, restore application settings outside the workspace, or guarantee an atomic snapshot across multiple SQLite databases and ordinary files.

## Authenticated encryption and key handling

Generate a key once, in a directory outside the Yuki workspace and backup destination:

```sh
mkdir -p "$HOME/.config/yuki" && chmod 700 "$HOME/.config/yuki" && python scripts/backup_crypto.py generate-key "$HOME/.config/yuki/backup.key"
```

Protect and separately back up this key using your operating system's secret-management facilities or an offline secure medium. The key file is exactly 32 random bytes. On POSIX, the tool creates it with mode 0600 and rejects broader permissions. On Windows, the file inherits its parent directory's ACL; create the key in a directory accessible only to the intended Windows account. **Do not commit the key, put it in the workspace, put it in the backup directory, or send it to Yuki's model/provider.** Loss of the key means the encrypted archives cannot be recovered.

Enable encryption for scheduled backups by setting `YUKI_BACKUP_KEY_FILE` in the scheduler's environment:

```sh
YUKI_BACKUP_KEY_FILE="$HOME/.config/yuki/backup.key" sh scripts/daily_backup.sh
```

The backup script then publishes a `.yukibak` file encrypted with AES-256-GCM. The file format authenticates its header and ciphertext; the tool streams data in bounded chunks, checks authentication before publishing decrypted output, and refuses to overwrite existing files. The key is not embedded in the archive. The backup scripts remain plaintext by default if this environment variable is absent, and print a warning. Install the repository's `requirements.txt` before using encryption.

To authenticate/decrypt an encrypted POSIX archive before restore:

```sh
python scripts/backup_crypto.py decrypt "/path/to/yuki-data-YYYYMMDD_HHMMSS.yukibak" "/private/staging/backup.tar.gz" --key-file "$HOME/.config/yuki/backup.key"
python scripts/restore_workspace.py "/private/staging/backup.tar.gz" "/path/to/new-restore-directory"
```

On Windows, use a `.zip` destination for the decrypted temporary archive and then restore it:

```bat
python scripts\backup_crypto.py decrypt "F:\YukiBackups\yuki-data-YYYYMMDD_HHMMSS.yukibak" "E:\PrivateStaging\backup.zip" --key-file "C:\Users\YOU\AppData\Local\YukiSecrets\backup.key"
python scripts\restore_workspace.py "E:\PrivateStaging\backup.zip" "E:\YukiRestoreTest"
```

Use a private staging directory on an encrypted local volume and securely remove the decrypted temporary archive after restore. Wrong keys, tampering, malformed headers and truncated ciphertext fail closed; the decryptor does not publish an output file unless GCM authentication succeeds. A successful decrypt is only cryptographic verification; the separate restore utility still validates archive paths and SQLite integrity.
## Security and restore

- Prefer encrypted storage in addition to archive encryption. Without `YUKI_BACKUP_KEY_FILE`, the scripts intentionally create plaintext archives.
- Keep at least one separate backup copy; do not use the same physical drive as the only backup.
- Never store backups in the Git repository; `BACKUP/` is ignored by Git.
- Archives may contain sensitive memories, account data, conversations, preferences, reminders and other private records. Protect access and remove old backups securely according to your device's storage policy.
- Test restoration into a **new empty directory** before relying on backups. On Unix, inspect with `tar -tzf backup.tar.gz` and extract with `tar -xzf backup.tar.gz -C /path/to/restore`. On Windows, use File Explorer or PowerShell `Expand-Archive` into a new folder.
- SQLite database files are snapshotted independently and checked for integrity, but this is not a cross-database transaction. Stop Yuki first for a consistent whole-workspace restore point.
- The snapshot helper rejects symlinks and special files rather than following them outside the workspace. If your workspace intentionally depends on symlinks, back up those targets separately and review the boundary before changing this behavior.
- **Production recovery is not complete.** Authenticated archive encryption and isolated restore are implemented, but the encrypted backup flow still needs a full Windows/POSIX CI pass, a tested operational key-management procedure, retention/deletion semantics review, crash/power-loss testing, and a documented disaster-recovery drill. Backups remain opt-in encrypted; public production recovery is not certified. Do not treat these scripts as a production disaster-recovery system yet.
