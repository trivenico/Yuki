# Phase 273 — Git-backed artifact and workspace provenance

## Delivered

Added `automation/reproducible_workspace.py` to connect the pinned Git
snapshot reader, deterministic snapshot builder, durable verification artifact
store, and a fresh materialized workspace.

The manager:
- Reads tracked file bytes from a full pinned Git commit.
- Encodes the exact source tree into a deterministic versioned JSON bundle.
- Persists that bundle through `VerificationWorkflow.record_artifact`.
- Materializes the exact source files into a newly named workspace and checks
  the expected snapshot digest.
- Fingerprints dependency manifests and lockfiles separately.
- Persists source commit, snapshot digest, dependency digest, workspace path,
  file count and byte count as provenance.
- Rejects unsafe workspace names and conflicting provenance records.

Tests: `tests/test_reproducible_workspace.py`.

## Boundaries

This phase does not install dependencies or execute project code. The workspace
is not an OS sandbox. Dependency fingerprints identify files; they do not prove
that an environment is hermetic or that packages are safe. The verification
executor must be configured to target the materialized workspace explicitly,
and its receipt must be bound to the artifact digest.

The manager stores a versioned JSON byte bundle in the existing artifact store.
It does not yet provide a patch generator or merge workflow.

## Verification status

Tests have been authored but not executed in this environment. No CI pass is
claimed.
