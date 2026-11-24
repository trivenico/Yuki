# Audit 077 — Container Security, Runtime Isolation, and Production Deployment Hardening

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** Docker image build context, runtime image defaults, guarded Docker execution, workspace mounts, host/runtime trust, and systemd host-agent hardening.

## Executive summary

The repository has useful defensive foundations: the guarded Docker argv builder requires a digest-pinned image, disables network access by default, uses a read-only root filesystem, drops Linux capabilities, sets no-new-privileges, runs as the invoking non-root UID/GID, and bounds memory, swap, CPU, PIDs, wall-clock time, and output. It rejects launch from a root service. A separate systemd host inventory example also applies a meaningful set of hardening directives.

These controls do not establish a production-grade isolation boundary by themselves. Docker daemon access remains a high-trust host capability, Docker daemon self-reported diagnostics are not attestation, and a bind-mounted workspace remains writable when the policy explicitly grants that capability.

## Confirmed findings

### P1 — Docker build context was not constrained

The repository has `docker/Dockerfile`, which uses `COPY . .`, but no root `.dockerignore` was present on the reviewed `main` branch. A developer building from the repository root could therefore send local files in the build context that are not intended to be part of the image, potentially including VCS metadata, local environment files, credential material, user databases, and generated data.

**Fix submitted:** add a root `.dockerignore` excluding VCS metadata, local virtual environments, environment/credential files, common key and certificate formats, cloud/SSH credential directories, runtime databases, user data, logs, and build artifacts. Example environment templates remain eligible for inclusion.

**Residual limitation:** Docker build context is determined by the directory passed to `docker build`. A root `.dockerignore` protects builds whose context is the repository root; it does not replace secret scanning or safe build-context selection. Dockerfile-specific ignore files, if introduced later, must preserve equivalent exclusions.

### P1 — Docker daemon trust remains a host boundary

The coding-task runner launches Docker CLI commands. A process with access to a privileged/rootful Docker daemon may effectively gain host-level control even when individual containers are configured restrictively. The inspected Python policy and Docker argv builder cannot establish daemon rootless mode, daemon authorization policy, or host-side network and mount enforcement.

The runtime capability model explicitly treats declarations as untrusted and rejects promotion to attestation. Keep this fail-closed behavior. Production deployment should use a dedicated rootless daemon or a separately isolated worker VM, with no shared Docker socket exposed to agent workloads.

### P1 — Sandbox capability declarations are not runtime proof

`SandboxRuntimeCapabilities` and `parse_docker_info` are diagnostic metadata. The code explicitly lists namespace, seccomp profile, mandatory access control, network egress, mount, secret exclusion, hard resource limit, cleanup, and probe provenance as unsupported verification. Do not report a sandbox as attested based only on Docker's self-reported `info` response or caller-supplied booleans.

### P2 — Writable workspace is an explicit capability

The guarded Docker argv builder mounts the workspace read-only by default and switches to read-write only when `write_workspace` is granted. This is preferable to a broad host mount, but the selected workspace may still contain secrets or symlinks/special content below the top-level directory. Use a dedicated disposable source snapshot and a separate bounded output area for untrusted execution; do not mount a developer home or general application data directory.

### P2 — Convenience Docker image is not a sandbox

`docker/Dockerfile` runs the application as a non-root user, which is a useful baseline. The image uses a mutable Python base tag and installs dependencies from a range-based requirements file. The Docker README correctly warns that this image is not a sandbox and should not be used to execute untrusted code. Image reproducibility, base-image digest pinning, vulnerability scanning, signing, and provenance remain separate supply-chain controls.

### P2 — Separate legacy sandbox runner has weaker controls

`hackback/sandbox.py` is separately opt-in and uses a digest-pinned allow-listed image, no network, read-only root, dropped capabilities, no-new-privileges, resource bounds, and a non-root container user. It still uses a writable bind mount of the configured workspace, a configurable Docker binary string, and `subprocess.run(capture_output=True)` before truncating the returned strings. The capture therefore is not an actual memory/output bound. Keep this runner disabled for untrusted workloads until it is consolidated with the bounded runner and receives equivalent executable-path, output-drain, and daemon-isolation guarantees.

## Positive controls observed

- Main sandbox policy defaults to network disabled, read-only root, and non-root execution.
- Docker argv builder requires immutable SHA-256 image references and uses `--pull=never`.
- Docker invocation drops all capabilities, applies no-new-privileges, uses PID/memory/swap/CPU limits, and creates a bounded noexec/nosuid/nodev temporary filesystem.
- Workspace is read-only unless a specific write capability is granted.
- Root service execution is refused by the main Docker argv builder.
- The systemd host inventory example uses a dedicated service account, `NoNewPrivileges`, private temporary storage, strict system protection, protected home/kernel/cgroups, no capabilities, and restricted address families.
- The main runner bounds wall-clock time and captured output and treats failed container cleanup as an execution failure.

## Recommended next controls

1. Deploy the worker on a dedicated rootless runtime or disposable VM; isolate the Docker daemon from the main Yuki control plane.
2. Add a trusted host-side verifier for runtime configuration and prove namespace, seccomp, AppArmor/SELinux, cgroup, mount, egress, and cleanup behavior with adversarial tests.
3. Replace the writable whole-workspace mount with immutable source snapshot plus separate quota-limited output mount.
4. Consolidate `hackback/sandbox.py` into the hardened execution service or remove its ability to run untrusted code.
5. Pin the convenience Docker base image by digest, use a resolved/hash-verified dependency set, scan the resulting image, and publish signed provenance.
6. Add container build-context secret scanning and CI tests that verify sensitive file patterns remain excluded.
7. Test resource exhaustion, metadata/loopback access, host sentinel-file access, socket/device access, cancellation, daemon failure, and cleanup on supported production hosts.

## Validation limits

This audit is based on source inspection of the paths listed above. No Docker daemon, container escape harness, production host, systemd service, image build, packet capture, or resource-exhaustion test was run. The new `.dockerignore` has not been validated by an actual Docker build. No claim of production readiness or sandbox attestation is made.
