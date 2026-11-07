# Phase 272 — Git-backed reproducible source snapshots

## Delivered

Added `automation/git_repository_snapshot.py`, which reads tracked regular
blobs from an exact full Git commit object ID and feeds those bytes into the
existing deterministic snapshot builder.

The reader:
- Requires a full hexadecimal commit object ID (not a branch or tag name).
- Resolves and checks the exact commit before reading its tree.
- Uses Git argument arrays with `shell=False`, no stdin, disabled system/global
  Git configuration, disabled terminal prompts, and bounded command timeouts.
- Reads the committed tree, not the potentially dirty working tree.
- Enforces tree-output, file-count, per-file and aggregate snapshot limits.
- Rejects symlinks and gitlinks/submodules rather than following them.
- Applies the snapshot builder's generated-directory exclusions.
- Returns the resolved commit, deterministic manifest and exact file bytes.

Tests: `tests/test_git_repository_snapshot.py`.

## Deliberate limitations

This is a source reader, not a hermetic build environment or sandbox. It does
not resolve dependencies, fetch submodules, execute project code, or provision
an isolated OS workspace. Git itself must be installed and the repository must
already contain the requested commit. The next step is integrating this reader
with artifact provenance and materialization, then running verification in an
OS-restricted worker.

## Verification status

Tests are authored but not executed here. No CI success is claimed.
