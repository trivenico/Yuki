# Yuki isolated execution threat model

**Status:** design baseline; implementation and independent security validation are outstanding.  
**Scope:** generated code, repository tests, build steps, and other untrusted developer workloads.

## 1. Security objectives

Yuki must be able to prepare, inspect, and—only after explicit authorization—execute bounded developer workloads without granting those workloads the permissions of the Yuki process or the owner account.

The execution boundary must provide:

- Isolation from the host OS, owner files, credentials, and unrelated processes.
- No network access by default, including metadata-service and loopback access outside the sandbox.
- Read-only source inputs and a separate bounded writable scratch/output area.
- Enforced CPU, memory, process-count, file-size, disk, and wall-clock limits.
- A pinned, reviewed runtime image and deterministic invocation contract.
- Explicit approval bound to the exact workload, source digest, image digest, policy, and target.
- Bounded output, structured audit events, cancellation, cleanup, and reproducible result metadata.
- No fallback to host execution if the isolation backend is missing, misconfigured, or unhealthy.

## 2. Assets and trust boundaries

### Assets to protect

1. Owner credentials, API tokens, SSH keys, browser profiles, and OS credential stores.
2. Personal files, project files outside the selected workspace, and private application data.
3. Host integrity, availability, network identity, and other running processes.
4. Yuki's approval state, task queue, audit ledger, and trusted configuration.
5. Repository integrity, release artifacts, signing keys, and deployment credentials.
6. Confidential source code and generated outputs.

### Trust zones

| Zone | Trust level | Rule |
|---|---|---|
| Owner approval interface | Trusted only after authenticated confirmation | Approval is explicit, scoped, and digest-bound |
| Yuki control plane | Trusted computing base | Owns policy, identity, approvals, dispatch, and audit |
| Source snapshot / proposed patch | Untrusted input | Validate and mount read-only |
| Sandbox workload | Untrusted | Assume arbitrary code execution and compromise |
| Sandbox output | Untrusted | Treat as data; validate before import |
| Host OS and runtime | Trusted computing base | Harden, patch, minimize, and monitor |
| External network / registries | Untrusted | Pin artifacts and verify provenance |

A container is not automatically a sufficient security boundary. The selected runtime, kernel, configuration, host privileges, and mount topology all affect the actual isolation guarantee.

## 3. Threat actors and abuse cases

- **Malicious or compromised dependency:** executes install hooks, test discovery hooks, or import-time code to read secrets or persist on the host.
- **Prompt-injected repository content:** attempts to convince an agent to expose credentials, expand mounts, enable networking, or bypass approvals.
- **Compromised model/provider output:** emits hostile code, paths, commands, or output designed to exploit a runner.
- **Malicious test workload:** reads host files, probes metadata endpoints, forks processes, consumes resources, tampers with neighboring tasks, or exfiltrates data.
- **Compromised sandbox image or registry artifact:** introduces a backdoor into every execution.
- **Confused-deputy or approval replay:** reuses an approval for different code, target, image, policy, or workspace state.
- **Runtime failure / cleanup race:** leaves a workload running after cancellation, or returns output from a stale execution.
- **Output parser attack:** uses huge, malformed, binary, or terminal-control output to corrupt logs or downstream consumers.

## 4. Required controls

### Control plane and authorization

- Authenticate the owner through the configured trusted-principal mechanism; an actor ID string alone is not authentication.
- Bind approval to a canonical execution manifest containing source snapshot digest, selected test target, runtime image digest, policy version, resource limits, and output policy.
- Re-authorize immediately before dispatch; reject expired, replayed, or mismatched approvals.
- Keep agent planning and sandbox execution separate. A specialist cannot grant itself permissions or approve its own workload.
- Do not pass secrets into the workload environment, filesystem, mounts, command arguments, or logs.
- Do not expose the container runtime socket, host PID namespace, host network namespace, or privileged mode.

### Runtime isolation

- Prefer a rootless OCI runtime with user namespaces and enforced seccomp plus AppArmor/SELinux where supported; consider a microVM boundary for higher-risk workloads.
- Never run as host root. Drop all Linux capabilities; use no-new-privileges.
- Use a private PID, mount, IPC, and network namespace. Network must be disabled by default and enforced outside the workload, not merely by convention.
- Do not mount the host home directory, host procfs, writable sysfs, devices, Docker/Podman sockets, credential stores, or arbitrary host paths.
- Mount the selected source snapshot read-only. Provide a separate size-limited scratch/output mount.
- Use an immutable image referenced by digest, not a mutable tag. Verify image provenance and maintain an allow-list.
- Use cgroups v2 or equivalent hard limits for memory, CPU, PIDs, and I/O; enforce wall-clock timeout and bounded output/disk quotas.
- Use ephemeral instances and destroy them after completion, timeout, cancellation, or error.
- If a required control is unavailable, reject execution. Never silently weaken the policy.

### Input, output, and lifecycle

- Resolve and validate paths before constructing the execution manifest; reject traversal, symlinks, special files, and paths outside the selected snapshot.
- Do not accept arbitrary shell strings from model output. Use a fixed entrypoint and structured arguments.
- Cap stdout, stderr, artifacts, file count, and total output size. Record truncation explicitly.
- Treat all artifacts and logs as untrusted. Scan/validate before they enter the main workspace or trigger another tool.
- Cancellation must terminate the complete sandbox process tree and verify teardown.
- Record execution ID, actor, approval ID, manifest digest, source/image/policy digests, timestamps, exit status, timeout/cancellation state, resource usage, and output digests. Never log secret values.
- Importing artifacts into the main workspace is a separate operation requiring a diff review and fresh digest-bound approval.

## 5. Existing Yuki behavior and known gaps

- ApprovedTestRunner is a host subprocess runner. It uses a fixed Python module invocation, allow-list, approval callback, timeout, and audit callback, but its child process still executes with the host user's permissions. It is suitable only for trusted code and is not a sandbox.
- run_approved_regression_candidate rejects this host runner. It must remain fail-closed until a separately implemented and validated sandbox backend exists.
- The existing workspace proposal, diff, impact, and regression planning layers are preparation/review components; they do not provide isolation.
- `core/sandbox_policy.py` defines restrictive policy values. `core/sandbox_runtime.py` builds Docker argv and validates a capability declaration, but the argv builder does not launch Docker or probe the daemon. The capability record is metadata and is not proof of enforcement.
- The Docker argv builder now requires a sha256-pinned image, disables pulling at run time, bounds arguments, rejects symlink/root/comma-containing workspace paths, disables networking, drops capabilities, sets no-new-privileges, and constrains memory, swap, CPU, and PIDs. It still does not prove the daemon is rootless or that host-side controls are active.
- `hackback/sandbox.py` is a separate opt-in Docker runner and is not integrated with the regression-plan adapter. Its writable workspace mount and daemon trust assumptions require additional review; use only on a disposable host with a dedicated workspace containing no secrets.
- A container image alone, a subprocess timeout, a cleared environment, or a path allow-list does not prove sandbox isolation.
- No production sandbox backend is claimed by this document.

## 6. Sandbox backend acceptance gates

A backend is not eligible for regression workflow integration until all gates pass:

1. **Capability attestation:** reports runtime type/version, rootless status, namespaces, seccomp, mandatory access control, cgroup limits, network policy, and mount policy. Missing required capabilities fail closed.
2. **Manifest integrity:** approval binds the canonical manifest and all relevant digests; any mutation invalidates approval.
3. **Filesystem tests:** workload cannot read sentinel files outside the snapshot, follow symlinks into host paths, write source mounts, or access host sockets/devices.
4. **Network tests:** outbound, DNS, loopback-to-host, link-local, and cloud metadata access are blocked by default.
5. **Privilege tests:** workload cannot gain host root, capabilities, host namespaces, or access runtime control sockets.
6. **Resource tests:** CPU, memory, process, disk, output, and timeout limits are enforced under adversarial workloads.
7. **Lifecycle tests:** cancellation, timeout, crash, and daemon restart leave no running workload or writable mount behind.
8. **Output tests:** malformed, oversized, binary, and terminal-control output is safely bounded and represented.
9. **Audit tests:** denied, started, completed, failed, timed-out, cancelled, and cleanup-failure outcomes are attributable and tamper-evident within the supported audit model.
10. **Integration tests:** regression execution accepts only the concrete sandbox backend; host runner and backend spoofing remain rejected.
11. **Operational review:** supported OS/runtime matrix, patch cadence, image update process, incident response, and rollback are documented.
12. **Independent review:** security review of runtime configuration and adversarial tests is completed before production enablement.

## 7. Residual risks and release decision

Containers share a host kernel; kernel vulnerabilities and runtime escapes remain possible. Rootless operation and defense-in-depth reduce risk but do not eliminate it. A microVM may provide a stronger boundary at additional operational cost, but it too requires hardening and validation.

Until the acceptance gates are met, Yuki must not execute untrusted generated code on the host. The developer workflow may continue to analyze source, prepare diffs, recommend tests, and request review, but execution remains unavailable for untrusted workloads.

**Release decision:** sandboxed developer execution is blocked. Do not mark this capability complete based only on an adapter, a policy object, or a container launch succeeding.
