# Daily backup scripts

These scripts make timestamped archives of Yuki's **local data directory**. They do not upload data, install software, encrypt archives, or guarantee that an application-specific database is transactionally consistent. Stop Yuki before backing up if its JSON files may be changing.

## Linux / macOS

From the repository root:

```sh
sh scripts/daily_backup.sh
```

Defaults:
- Source: `YUKI_HOME` if set, otherwise `./YukiData`
- Destination: `YUKI_BACKUP_DIR` if set, otherwise `./BACKUP/YukiData`
- Retention: 14 days

Override source and destination with positional arguments:

```sh
sh scripts/daily_backup.sh "/media/my-ssd/YukiData" "/media/backup-drive/YukiBackups"
```

Optional retention setting: `YUKI_BACKUP_RETENTION_DAYS=30`.

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

Defaults and environment overrides match the Unix script: `YUKI_HOME`, `YUKI_BACKUP_DIR`, and `YUKI_BACKUP_RETENTION_DAYS`. The script uses the Windows-provided PowerShell `Compress-Archive` command and does not install packages.

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
- These scripts archive files as they are found. Stop Yuki first to avoid copying files while they are being updated. They do not perform database-aware snapshots.
