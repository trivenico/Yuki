# Phase 278 — Pinned Git workspace integration

## Configuration

Set `YUKI_GIT_REPOSITORY` to the local path of the Git repository Yuki is
authorized to read. If unset, pinned snapshot capture remains disabled. Startup
does not clone repositories, fetch remote content, or change Git refs.

The manager is initialized using the existing artifact workflow and stores
workspace provenance in `yuki_workspace_provenance.sqlite3` under Yuki's
configured workspace.

## Commands

- `/engineering snapshot PLAN_ID SUBTASK_ID FULL_COMMIT_SHA` captures a full
  commit SHA from the configured repository after operator confirmation. The
  subtask must exist, have a dispatched task, and not be terminal.
- `/engineering provenance ARTIFACT_ID` prints the persisted source commit,
  artifact and snapshot digests, dependency manifest digest, dependency files,
  file count, byte count, and materialized workspace path.
- `/engineering status` reports whether the Git workspace manager initialized.

Snapshot workspace names are generated from a validated commit prefix plus a
random suffix, preventing collisions between repeated captures. The underlying
snapshot builder enforces path, file-count, file-size, total-size, digest, and
no-overwrite checks.

## Trust boundaries and limitations

- The Git reader resolves and reads an exact full commit object ID; uncommitted
  working-tree changes are not included.
- Only tracked regular blobs are accepted; symlinks and submodules are rejected.
- The snapshot is stored as a durable artifact and its provenance is separately
  recorded in SQLite.
- Snapshot capture does not run project code or dependency installation.
- A materialized workspace is **not** an OS/container sandbox. Do not run
  untrusted project commands against it without an independently configured
  sandbox boundary.
- The default local worker runtime still has no verification command allowlist.
  This phase intentionally does not enable arbitrary repository test commands
  or claim test evidence.
- A captured workspace is not yet automatically attached to a specialist worker;
  it is an operator-controlled source/provenance operation.

## Validation

Changes were committed to the feature branch and the startup source was fetched
back for inspection. Automated tests were not executed in this environment.
