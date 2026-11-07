# Phase 280 — Docker-isolated verification execution

## Scope

This phase adds a Docker-backed execution path that is distinct from the existing
host process executor. It is opt-in and invoked only by the interactive operator.

Configure `YUKI_GIT_REPOSITORY` as before, and set
`YUKI_VERIFICATION_IMAGE` to an immutable image reference such as
`python:3.12-slim@sha256:<64-hex-digest>`. The image must already be present
locally; Docker is invoked with `--pull=never`. Yuki does not pull arbitrary images.

Run:

`/engineering sandbox-verify ARTIFACT_ID`

Yuki first revalidates the immutable artifact and every materialized file, then
asks for high-risk operator confirmation. The only built-in command profile is
the fixed `unit-tests` argv: `python -m pytest -q`. User input cannot supply
container argv. The runner verifies the workspace snapshot digest again
immediately before launching Docker.

## Container restrictions

- Network disabled (`--network=none`).
- Root filesystem read-only, source bind mount read-only.
- Non-root UID/GID 65532.
- All Linux capabilities dropped.
- `no-new-privileges` enabled.
- PID, memory, memory+swap, CPU, wall-time, and output limits.
- Small `/tmp` tmpfs mounted with `noexec,nosuid,nodev`.
- No host home, Docker socket, or application credentials mounted.
- Minimal child environment; stdin closed; no shell invocation.
- Immutable image digest required and implicit image pulls disabled.
- Execution receipt stores image, artifact digest, command, exit state, and output
  digest in `yuki_sandbox_verification.sqlite3`.

## Important limitations

Docker isolation is a useful local boundary, not a substitute for a hardened
multi-tenant VM sandbox. The Docker daemon and host kernel remain trusted. This
runner does not claim protection against all kernel/daemon vulnerabilities,
side channels, or malicious container-runtime configurations. Use a dedicated
rootless Docker daemon or disposable VM for stronger isolation, and do not expose
the Docker socket to agent-controlled processes.

The execution receipt is not yet converted into an independent verification
report or engineering acceptance decision. A passing test command is evidence
only for the exact artifact digest and pinned image shown in the receipt. It does
not by itself authorize a release or self-upgrade.

The built-in pytest profile assumes pytest is installed in the pinned image.
No dependencies are installed during execution, and network access is disabled.

## Validation

Added unit tests for hardened Docker argument construction, durable receipt
persistence, digest-pinned image enforcement, and refusing a tampered workspace
before process launch. Automated tests and a live Docker execution were not run
in this environment. Configure a digest-pinned image and run CI before enabling
this workflow on important repositories.
