# Yuki Continuity, portability and recovery

## Source of truth

The GitHub repository is the source of truth for versioned Yuki source code,
tests, migrations, safe configuration templates and architecture decisions.
Self-evolution must work on a dedicated branch, run the required tests and
security checks, and create a reviewable commit/PR. It must never rewrite
history or push autonomous changes directly to the protected default branch.

A clone restores the versioned code at the selected commit. A clone does not
restore local secrets, provider credentials, uncommitted work, local models,
or private runtime state.

## Private runtime state

Private state lives outside Git under `YUKI_HOME` (default
`<repository>/YukiData`). It may contain personal memory, tasks, workspace
files, and local databases. Do not commit this directory or backup archives
to the repository.

Create a local snapshot:

```sh
python scripts/yuki_continuity.py snapshot
```

Choose a separate encrypted backup location:

```sh
python scripts/yuki_continuity.py snapshot --destination "/path/to/encrypted/backup"
```

Verify an archive before moving or restoring it:

```sh
python scripts/yuki_continuity.py verify "/path/to/yuki-continuity-<timestamp>.zip"
```

Restore only into an absent or empty directory:

```sh
python scripts/yuki_continuity.py restore "/path/to/yuki-continuity-<timestamp>.zip" --destination "/path/to/YukiData"
```

The snapshot manifest records the source repository commit/branch/remote when
Git metadata is available, file names, file count and SHA-256 digests. Restore
verifies every digest before writing and refuses non-empty destinations.
Known credential files, key files, secret directories and symlinks are
excluded. This is a defense-in-depth filter, not a complete secret scanner.
Review backup contents and keep the backup volume encrypted.

## New computer recovery

1. Install Git and a supported Python version.
2. Clone the GitHub repository and checkout the desired reviewed commit.
3. Run the platform setup script to create a machine-specific virtual
   environment and install dependencies.
4. Obtain the private continuity archive from a separate encrypted backup
   location; verify it, then restore it into `YukiData` (or configure
   `YUKI_HOME` to the restored location).
5. Re-provision provider credentials on that computer using its local secret
   manager or environment. Never copy credentials into Git or a snapshot.
6. Run the test suite and a Yuki startup smoke test before resuming agents.

Windows uses `python scripts\\yuki_continuity.py ...` with the same arguments.

## Guarantees and limitations

- GitHub preserves committed, pushed repository history; uncommitted changes
  are not backed up by Git until committed and pushed.
- Private data snapshots are separate local archives and are not uploaded to
  GitHub automatically.
- The snapshot is not database-transaction-aware. Stop Yuki and background
  workers before taking a snapshot to avoid inconsistent live database files.
- The archive is integrity-checked, not encrypted. Store it only on an
  encrypted destination or encrypt it separately.
- A Python virtual environment and downloaded models are machine-specific;
  recreate/install them on each host rather than copying `.venv`.
- Hardware-specific integrations, OS permissions and optional system
  packages may need per-machine setup.
- Keep at least two backup copies on separate storage. Periodically perform a
  restore drill into a clean directory.
- Never automatically push private runtime data, credentials, user memories,
  logs, local models, or arbitrary workspace contents to GitHub.

## Self-evolution checkpoint policy

Before a self-upgrade:
1. Stop or quiesce affected workers.
2. Create a local continuity snapshot and verify it.
3. Record the current Git commit and working-tree status.
4. Apply changes on an isolated branch/worktree.
5. Run tests, security checks and migration compatibility checks.
6. Commit and push only allowlisted source artifacts.
7. Open a PR; require human review for merge, migration of user data, and
   production deployment.
8. Keep the prior commit and verified private snapshot available for rollback.

This is a documented workflow; automatic orchestration and enforcement of
every checkpoint remains future integration work.
