# Daily backup scripts

These scripts create timestamped archives of Yuki's configured local workspace. They do not upload data, install software, encrypt archives, or guarantee an application-consistent SQLite snapshot. Until database-aware backup is implemented, **stop Yuki before running them** so SQLite and other files are not changing during the archive.

## Linux / macOS

From the repository root:

```sh
sh scripts/daily_backup.sh
```

Source precedence:

1. First positional argument.
2. `YUKI_BACKUP_SOURCE` (explicit source override).
3. `YUKI_WORKSPACE` (workspace override; relative paths resolve under `YUKI_HOME` when set, otherwise repository root).
4. `YUKI_HOME` (data root).
5. `./yuki_workspace` under the repository root (default workspace).

The script does not parse a custom YAML config file. If your configured workspace comes from a custom config, set `YUKI_BACKUP_SOURCE` or pass the source path explicitly.

Destination and retention:
- Destination: `YUKI_BACKUP_DIR` if set, otherwise `./BACKUP/YukiData`
- Retention: `YUKI_BACKUP_RETENTION_DAYS`, default 14 days (1–3650)

Override source and destination with positional arguments:

```sh
sh scripts/daily_backup.sh "/media/my-ssd/YukiData" "/media/backup-drive/YukiBackups"
```

The script requires standard `tar`, `find`, and shell utilities. It validates the archive before publishing it and deletes only matching Yuki archive files older than the configured retention period.

## Windows

Double-click `scripts/daily_backup.bat` or run it from Command Prompt:

```bat
scripts\daily_backup.bat
```

You can pass source and destination directories:

```bat
scripts\daily_backup.bat "E:\YukiData" "F:\YukiBackups"
```

The same source precedence, `YUKI_BACKUP_DIR`, and `YUKI_BACKUP_RETENTION_DAYS` rules apply. The script uses the Windows-provided .NET ZIP APIs through PowerShell, includes hidden files, reads every archive entry to validate it, and does not install packages. For a custom YAML workspace, use `YUKI_BACKUP_SOURCE` or a positional source argument.

## Schedule it daily

Scheduling is intentionally **not automatic**.

- Linux/macOS: use your user crontab or OS scheduler to run the script once daily. Use absolute paths and log output to a private file. Example cron entry (edit paths for your machine):

  ```cron
  0 3 * * * /bin/sh "/absolute/path/to/Yuki/scripts/daily_backup.sh" >> "$HOME/yuki-backup.log" 2>&1
  ```

- Windows: create a Task Scheduler task that runs `cmd.exe` with arguments `/c "C:\absolute\path\to\Yuki\scripts\daily_backup.bat"`. Set the correct working directory and choose a time when the SSD is connected and unlocked.

Neither scheduler can back up an SSD that is disconnected. Test a manual backup and restore before scheduling unattended runs.

## Security and restore

- Prefer an encrypted SSD/backup volume. These scripts create archives but do not encrypt them.
- Keep at least one separate backup copy; do not use the same physical drive as the only backup.
- Never store backups in the Git repository; `BACKUP/` is ignored by Git.
- Archives may contain sensitive memories, health/habit data, reminders and other private records. Protect access and remove old backups securely according to your device's storage policy.
- Test restoration into a **new empty directory** before relying on backups. On Unix, inspect with `tar -tzf backup.tar.gz` and extract with `tar -xzf backup.tar.gz -C /path/to/restore`. On Windows, use File Explorer or PowerShell `Expand-Archive` into a new folder.
- These scripts archive files as they are found. Stop Yuki first to avoid copying files while they are being updated. They do not call SQLite's backup API, coordinate WAL checkpoints, encrypt data, or prove database restore integrity.
- Current backup readiness is **development-only**. Do not treat these archives as a production disaster-recovery mechanism. The remaining work includes encrypted, authenticated archives with externally managed keys; SQLite-consistent snapshots for each configured database; isolated restore; and wrong-key, tamper, truncation, path-traversal, and corruption tests.
