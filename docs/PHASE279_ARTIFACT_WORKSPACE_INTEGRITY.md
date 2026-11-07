# Phase 279 — Artifact workspace integrity verification

## Operator command

`/engineering verify ARTIFACT_ID` requires operator confirmation and then performs
a read-only integrity check. It does not launch a subprocess or execute repository
content.

The check:

1. Reads the durable artifact through `VerificationWorkflow.read_artifact`, which
   rechecks the stored byte length and SHA-256.
2. Validates the source-bundle schema and canonical Base64 encodings.
3. Reconstructs the file map and recomputes the deterministic repository snapshot.
4. Confirms snapshot digest, file count, and total byte count against provenance.
5. Confirms the materialized workspace remains under the managed workspace root.
6. Rejects a symlink workspace root, nested symlinks, and non-regular filesystem
   entries.
7. Reads all regular files and confirms there are no missing, changed, or unexpected
   files relative to the immutable bundle.

The result is explicitly described as a **source-integrity check**, not as a test
suite result, security scan, or independent engineering acceptance decision.

## Security boundary

Repository tests and build scripts are executable project code. A subprocess
allowlist alone does not make that code safe to execute on the host. Yuki therefore
does not automatically configure `pytest`, package installation, build scripts,
or arbitrary repository commands in the local verification executor.

Before test execution is enabled, a separately enforced sandbox is required:
container/VM isolation, no host credential mounts, no ambient network by default,
resource limits, read-only source plus explicit temporary output mounts, and
auditable pinned toolchain images. The verification receipt must bind the exact
artifact digest and the sandbox/toolchain identity.

## Tests added

- Successful verification of a newly captured snapshot.
- Detection of modified workspace content.
- Detection of unexpected files.

Automated tests were authored but not run in this environment. The startup status
output was also cleaned up to avoid a stale duplicate Git-manager status line.
