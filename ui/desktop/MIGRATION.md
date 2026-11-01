# Desktop user-data migration

Yuki Desktop stores installed application data under the operating system's
per-user application-data directory. Earlier development builds may have used
a repository-local `.yuki` directory.

The migration primitives in `ui.desktop.migration` are deliberately separate
from application startup. They do not run automatically. A future migration
screen must:

1. Identify the legacy source and current destination without opening either for writes.
2. Show the source location, destination, file count, and total size using `preview_migration`.
3. Explain that the source is retained and destination must be empty.
4. Obtain explicit confirmation from the authenticated local user.
5. Call `migrate_data` only after confirmation.
6. Report success only after the verified staging directory is atomically published.

The migration routine rejects symlinks and non-empty destinations. SQLite
files are copied with SQLite's backup API and integrity checked. Other files
are byte-copied and SHA-256 verified. A manifest is written into the new data
directory. The old source is never deleted or modified.

This is a local copy operation, not a backup system. Do not use it as a
substitute for encrypted backup/restore, and do not treat its manifest as a
signed provenance statement. Interrupted migration leaves the source intact;
a temporary staging directory may remain only if the process is forcibly
terminated before cleanup.
