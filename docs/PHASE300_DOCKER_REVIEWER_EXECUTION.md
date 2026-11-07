# Phase 300 — Docker-isolated reviewer workers

## Implemented

Adds `DockerReviewerExecutor`, a callable adapter for the host-managed
ReviewerExecutionAdapter.

- Requires a digest-pinned image and an explicit fixed argv command.
- Uses `--pull=never`, no network, read-only root filesystem, non-root UID,
  dropped Linux capabilities, no-new-privileges, default seccomp, and bounded
  tmpfs.
- Applies CPU, memory, PID, timeout, input, and combined stdout/stderr limits.
- Sends only a versioned JSON task plus base64-encoded assigned evidence on
  stdin. No host mounts are provided.
- Never serializes reviewer session tokens, signing keys, or ledger handles.
- Parses a strict result schema and validates it against the original task
  before returning a ReviewResult to the host adapter.
- Attempts container cleanup on timeout and output overflow.

## Runtime contract

The image entrypoint must read one JSON request from stdin and emit one JSON
object on stdout with exactly `status`, `summary`, and `findings`. Findings
must conform to ReviewFinding. Diagnostics belong on stderr. The image must
already be present locally and should be built from a reviewed, pinned
Dockerfile.

## Security boundary

This runner does not mount the repository or host filesystem. Evidence bytes
are intentionally supplied through a bounded input channel. The Docker daemon
and host kernel remain part of the trusted computing base. Container cleanup
is best-effort if the Docker daemon itself becomes unavailable. The session
token and signer remain in the host process.

## Tests

Five contract tests cover request scope, task-bound result parsing, malformed
output, and digest-pinned image enforcement. They are authored but not run
against a live Docker daemon in this environment.
