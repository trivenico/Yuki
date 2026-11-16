# Desktop user-data migration

Yuki Desktop stores installed application data under the operating system's
per-user application-data directory. Earlier development builds may have used
a repository-local `.yuki` directory.

The migration primitives in `ui.desktop.migration` are exposed in the native
desktop application's **Migration** tab. They remain separate from application
startup and never run automatically. The authenticated user must:

1. Identify the legacy source and current destination without opening either for writes.
2. Show the source location, destination, file count, and total size using `preview_migration`.
3. Explain that the source is retained and destination must be empty.
4. Obtain explicit confirmation from the authenticated local user.
5. Call `migrate_data` only after confirmation.
6. Report success only after the verified staging directory is atomically published.

Migration is limited to 20,000 files and 2 GiB per operation. The desktop
shows the full count and source byte size and lists the first 200 paths in the
review pane; larger path lists are summarized rather than fully rendered. The user must explicitly confirm
before the migration worker starts. The controller requires a live session
and rejects destinations that overlap the active Yuki profile or are symlinks.

The migration routine rejects symlinks and non-empty destinations. SQLite
files are copied with SQLite's backup API and integrity checked. Other files
are byte-copied and SHA-256 verified. A manifest is written into the new data
directory. The old source is never deleted or modified.

The UI writes the verified copy to a separate destination; it does not hot-swap
or activate the migrated profile. The user must separately configure or
activate a migrated data directory through a future, reviewed recovery flow.

This is a local copy operation, not a backup system. Do not use it as a
substitute for encrypted backup/restore, and do not treat its manifest as a
signed provenance statement. Interrupted migration leaves the source intact;
a temporary staging directory may remain only if the process is forcibly
terminated before cleanup.
