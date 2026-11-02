# Workspace Snapshot & Recovery

This initial service provides immutable, content-addressed snapshots for explicitly
selected regular files, plus integrity-checked compare-and-swap restore.

## Usage

\`\`\`python
from automation.workspace_snapshots import WorkspaceSnapshotStore

snapshots = WorkspaceSnapshotStore(
    workspace_root="/workspace/project",
    storage_root="/var/lib/yuki/snapshots/project",
    authorize=application_policy,
)
snapshot = snapshots.create(actor_id="alice", paths=["src/app.py", "tests/test_app.py"])
# Before restore, calculate current hashes and present the restore action for approval.
snapshots.restore(
    actor_id="alice",
    snapshot_id=snapshot.snapshot_id,
    expected_current={"src/app.py": current_sha256},
)
\`\`\`

## Security and current limits

- Snapshot storage must be outside the workspace and is created with restrictive permissions.
- Authorization is checked for snapshot create, read and restore.
- Symlinks, traversal, VCS metadata, dependency directories, environment files and secret-like paths are excluded.
- File count, per-file size and total snapshot size are bounded.
- Blobs are content-addressed and verified before restore.
- Restore requires compare-and-swap hashes to avoid overwriting concurrent edits.
- This first version snapshots existing files only; it does not capture deletions, new-file absence, directories, ACLs, extended attributes or full workspace state.
- Restore prepares temporary files before replacement, but multi-file filesystem replacement is not globally atomic. A crash during replacement requires operator inspection.
- No automatic cleanup/retention policy is included. Back up the snapshot store itself and test recovery before production use.
- The service is not a sandbox and does not replace approvals in the engineering workflow.

## Tests

\`\`\`bash
python -m unittest tests.test_workspace_snapshots
\`\`\`
