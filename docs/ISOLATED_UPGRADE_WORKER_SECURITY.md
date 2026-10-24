# Isolated upgrade worker — security contract

Status: **design/acceptance contract only; no production sandbox is implemented by this document.**

The current self-upgrade coordinator defaults to denying host-side test execution. Do not set `allow_host_test_execution=True` for untrusted proposals. A disposable directory, subprocess, test allow-list, digest-bound approval, or successful CI run is not an operating-system sandbox.

The current Podman adapter's workspace file access requires POSIX `O_NOFOLLOW`, `O_DIRECTORY`, and `dir_fd` support. It fails closed when those primitives are unavailable; the worker filesystem-contract unit suite is skipped on unsupported platforms. The intended runtime target is a Linux host with rootless Podman, and the exact deployment host must still pass the opt-in integration contract. Descriptor-relative traversal protects file and parent-directory lookups beneath the opened workspace root, but it does not replace a private staging area, a patched kernel/runtime, or an independent security review.

## Trust boundaries

- **Controller (trusted):** authenticates the requester, obtains approval, computes immutable content digests, submits a bounded artifact, records the job, and decides whether a result is acceptable.
- **Worker (untrusted execution zone):** executes proposed code and tests. Treat all worker output, exit codes, files, and timing as untrusted claims.
- **Artifact handoff:** the worker receives only a content-addressed staged archive. It must not receive the host workspace path, credentials, repository write token, cloud metadata access, container socket, or controller secrets.
- **Promotion:** the worker cannot merge, deploy, modify the source workspace, change permissions, or grant itself capabilities. Promotion remains a separate human-approved operation.

## Required runtime controls

A production implementation must fail closed unless every required control is verifiably available:

1. A dedicated rootless container runtime or equivalently hardened sandbox, pinned to an immutable worker image digest.
2. Network disabled by default; no host networking, DNS, package installation, or metadata-service access.
3. Read-only root filesystem and read-only staged source; a size-limited, no-exec temporary filesystem only where tests need scratch space.
4. Non-root UID/GID, all Linux capabilities dropped, no-new-privileges, seccomp/AppArmor or equivalent policy, and no host PID/IPC namespaces.
5. Explicit CPU, memory, process-count, disk, artifact-size, output-size, and wall-clock limits.
6. An external supervisor that terminates the entire container/cgroup on timeout or cancellation and confirms cleanup. Killing only the CLI client is not sufficient.
7. Bounded output collection that continues draining or safely terminates the worker when output exceeds the cap; never buffer unbounded stdout/stderr in controller memory.
8. Strictly validated module targets and immutable input manifests. No model-generated shell command, executable path, image, mount, environment variable, or runtime flag is accepted.
9. A unique job ID and a digest over the staged artifact, test manifest, worker image digest, and runtime policy. The controller verifies these values on result receipt.
10. Audit records for queued, approved, started, denied, timed out, cancelled, completed, cleanup-failed, and audit-unavailable outcomes. Audit failure must prevent accepting a result.
11. No credentials in the worker environment. Secrets must not be mounted or forwarded, including through inherited environment variables.
12. Worker failure, daemon unavailability, malformed output, missing result, digest mismatch, timeout, or cleanup failure means **validation failed**. No promotion follows.

## Result acceptance

The controller may accept a result only if all of these are true:

- the job ID matches the submitted job;
- the staged-artifact digest, test-manifest digest, image digest, and policy version match the approved request;
- the worker supervisor reports successful completion and confirmed cleanup;
- every required test target ran and returned success;
- stdout/stderr and structured metadata are within configured bounds and schema;
- the final audit event was durably recorded.

A worker's self-reported `sandboxed=true` field is not proof of isolation. Runtime properties must be established by the trusted supervisor/configuration, not by the executed process.

## Required adversarial tests before enabling untrusted proposals

- Attempts to read environment variables, home directories, SSH/cloud credentials, host files, and container sockets.
- Attempts to write outside the workspace, modify staged input, access the network, resolve DNS, or reach metadata endpoints.
- Fork/process bombs, memory exhaustion, huge files, and stdout/stderr floods.
- Infinite loops, ignored termination, child processes surviving timeout, and cancellation races.
- Symlink/path traversal and malformed artifact/archive entries.
- Image-tag substitution, digest mismatch, stale approval, changed test source, and replayed result.
- Runtime daemon loss, supervisor crash, cleanup failure, audit-store failure, malformed result, and duplicate job delivery.
- Cross-platform controller tests plus integration tests on the exact production worker runtime.

## Rollout gate

Keep host execution disabled for untrusted proposals. Do not describe the system as sandboxed or production-ready until the runtime adapter, supervisor, immutable artifact protocol, adversarial tests, and operational monitoring are implemented and reviewed. Start with a non-production canary and no promotion permissions.
