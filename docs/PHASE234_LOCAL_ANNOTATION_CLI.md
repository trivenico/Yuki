# Phase 234 — Local CLI annotation runtime integration

## CLI

Install the optional Textual extra with `pip install -e ".[ui]"`, then launch
`yuki annotation`. The command uses the configured Yuki workspace and the
same stable local owner identity as persistent local conversations.

## Storage and security

- Annotation SQLite data is stored separately at
  `<workspace>/annotation/workbench.sqlite3`.
- The annotation directory is created with owner-only permissions (0700 on POSIX);
  SQLite database files are restricted to owner-only permissions (0600).
- Symlinked annotation directories and database paths are rejected.
- The owner identifier is a local ownership scope, not a network credential.
- SQLite connections are opened and closed per store operation; the runtime does
  not retain a connection or require a shutdown hook.
- The optional UI is imported only when the annotation command is invoked.
  Standard CLI commands do not acquire a Textual dependency.

## Explicit limitations

The TUI still uses JSON payload entry for all task families. Dedicated editors
for bounding boxes, polygons, audio segments, diarization and video tracking,
asset previews, import profiles, and marketplace export/delivery remain future
phases. No marketplace account, task feed, or payment integration is created.
