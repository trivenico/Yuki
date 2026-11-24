# Audit 096 — Agent Runtime Sandboxing, Capability Enforcement, and Side-Effect Containment

Repository: trivenico/Yuki  
Base: main  
Scope: sandbox policy/runtime, coding task runner, legacy hackback Docker runner, browser skill and network request controls.  
Outcome: one implementation fix submitted, plus audit and residual architecture findings.

## Executive summary

Yuki has a meaningful sandbox policy foundation. The core Docker argv builder defaults to no networking, read-only root, non-root UID, dropped capabilities, no-new-privileges, bounded memory/CPU/processes, digest-pinned images, and read-only workspace mounts unless write capability is explicit. Allowlist networking fails closed without a separate egress gateway. Runtime capability declarations are explicitly treated as untrusted, and validation refuses to treat self-reported Docker info as host attestation.

A concrete executable-resolution defect was found in the core sandbox path: launch and cleanup invoked Docker by the bare executable name, allowing inherited PATH resolution to select an unintended binary. Implementation PR #1166 changes the builder to require an absolute Docker executable path (default /usr/bin/docker) and passes the exact same path to cleanup. Tests were added but not executed.

Important residuals remain. Docker daemon access is still a privileged host boundary; no trusted runtime attestation is implemented. The separate legacy hackback sandbox still accepts a relative Docker executable, always mounts its workspace read-write, and captures complete stdout/stderr in memory before truncating returned strings. Browser request filtering does not pin Chromium's actual TCP destination, so DNS rebinding remains an infrastructure-level concern.

## Files inspected

- core/sandbox_policy.py
- core/sandbox_runtime.py
- core/coding_task_runner.py
- hackback/sandbox.py
- automation/browser.py
- skills/browser_skill.py
- tests/test_sandbox_runtime.py
- tests/test_coding_task_runner.py

## Positive controls observed

1. SandboxPolicy is frozen and requires immutable capability collections.
2. Policy bounds timeout, memory, CPU, output size, and process count; rejects boolean-as-integer values.
3. Default network mode is none; allowlist mode fails closed without a separately enforced egress gateway.
4. Core Docker argv uses digest-pinned images, read-only root, non-root UID, dropped Linux capabilities, no-new-privileges, bounded memory/CPU/PIDs, and a bounded tmpfs.
5. Workspace is mounted read-only unless write_workspace is explicitly granted.
6. Core coding runner requires exact True approval for each invocation and passes the exact execution request to approval.
7. Core runner uses argv (no shell), bounded command count/size, bounded output collection, timeout handling, and confirmed container cleanup.
8. Runtime capability declarations and Docker info are explicitly not treated as proof; validate_runtime_capabilities fails closed pending trusted host attestation.
9. Browser skill is read-only, HTTPS allow-list constrained, disables downloads and service workers, and frames page text as untrusted.

## Findings

### P1 — Docker executable was resolved through inherited PATH in the core sandbox path

Before PR #1166, build_docker_argv returned a command beginning with bare docker, and _kill_container independently launched bare docker. Both operations used PATH resolution. If a service process inherited a writable or attacker-controlled earlier PATH entry, an unintended executable could be invoked with the sandbox runtime's privileges. Independent resolution also meant cleanup was not guaranteed to use the same binary as launch.

Remediation submitted in PR #1166:
- Add a bounded absolute docker_binary path to build_docker_argv, defaulting to /usr/bin/docker.
- Reject relative and NUL-containing executable paths.
- Use the pinned executable for launch.
- Pass that exact executable to cleanup and reject relative cleanup paths.
- Add regression tests for relative-path rejection and absolute-path preservation; update coding runner fixtures.

Tests were added but not executed. Absolute path pinning mitigates PATH substitution; it does not by itself prove the file is root-owned, immutable, or otherwise protected from replacement. Deployment must provision the executable from a trusted, non-writable location.

### P1 — Docker daemon access remains a host-level trust boundary

The core runner invokes the Docker CLI. Access to a privileged Docker daemon can permit host-level control, depending on daemon configuration and runtime mode. Container flags cannot compensate for an attacker who controls the daemon or its socket.

Required direction:
- Prefer a dedicated rootless daemon or isolated worker VM with no host-sensitive mounts.
- Do not expose the host Docker socket to agent-controlled containers.
- Restrict daemon socket permissions to a dedicated service identity.
- Use an ephemeral worker host/VM for untrusted code and destroy it after execution.
- Verify isolation from the host, not from container self-report.

### P1 — Runtime capability attestation is not implemented

SandboxRuntimeCapabilities is a declaration model, and Docker info parsing is explicitly diagnostic. validate_runtime_capabilities intentionally refuses all capability claims until a trusted host-side verifier exists.

Risk: callers cannot yet obtain a positive proof that namespace, seccomp, MAC, cgroup, egress, mount, secret-exclusion, and cleanup controls are actually active. This is an acknowledged fail-closed limitation rather than a false claim of protection.

Required direction:
- Implement a trusted host-side probe with authenticated provenance.
- Verify actual container/VM configuration and effective cgroup, namespace, mount, seccomp, MAC, and network state.
- Bind the probe result to runtime instance, policy digest, image digest, and short expiry.
- Refuse execution if the verifier is unavailable or stale.

### P1 — Legacy hackback sandbox retains weaker executable, mount, and output boundaries

hackback/sandbox.py is a separate runner from core/coding_task_runner.py. It accepts docker_binary as a configurable string with default docker, so relative executable resolution remains possible in this legacy path. It always mounts the workspace read-write, unlike the core policy's read-only-by-default behavior. It uses subprocess.run(capture_output=True), retaining complete stdout and stderr in memory before truncating returned strings to 12,000 characters.

Risk:
- PATH substitution can redirect Docker invocation.
- A sandboxed process can modify all content in the mounted workspace.
- A noisy process can exhaust host memory through captured output even though the returned strings are truncated.

Required direction:
- Deprecate or route this runner through the core SandboxPolicy and core coding runner.
- If it must remain, require an absolute trusted Docker executable, default to read-only workspace, and make write access an explicit approved capability.
- Replace capture_output with streaming bounded output collection and terminate on output budget exhaustion.
- Add parity tests to ensure legacy entry points cannot bypass core policy.

### P1 — Browser URL validation does not pin Chromium's actual network destination

automation/browser.py resolves and validates the initial hostname before navigation and intercepts requests to ensure HTTPS and allow-listed hostname. However, Chromium performs its own DNS resolution when opening sockets. Route validation of URL host and scheme does not pin the TCP destination to the previously validated IP. DNS rebinding may therefore remain possible between validation and connection.

Required direction:
- Route browser traffic through a trusted egress proxy that resolves, validates, and pins destinations.
- Block private, loopback, link-local, metadata, and internal ranges at the network layer.
- Ensure redirects, subresources, WebSockets, and alternate protocols are covered by the same egress policy.
- Do not describe route interception as DNS pinning.

### P2 — Writable workspace mounts need immutable input and isolated output

Core sandbox supports explicit write_workspace, and the legacy runner always mounts RW. A writable host workspace means untrusted code can alter source files, configuration, or other data available in that directory. A separate output directory does not exist as a universal enforced contract in the inspected paths.

Required direction:
- Mount immutable source snapshots read-only.
- Provide a separate, narrowly scoped output mount.
- Validate output paths and file types before importing artifacts into trusted workspace.
- Keep credentials, .git, home directories, sockets, and runtime databases outside mounted paths.

### P2 — Container controls do not equal VM-grade isolation

The core Docker argv provides strong container hardening flags, but the reviewed code does not establish an independent kernel boundary. The Docker daemon and host kernel remain in the trusted computing base.

Required direction:
- Use microVM/VM isolation for high-risk arbitrary code, browser extensions, and untrusted plugin execution.
- Apply CPU, memory, disk, process, syscall, and wall-clock limits at the host boundary.
- Keep worker images minimal, signed, digest-pinned, patched, and reproducibly built.
- Destroy workers after each high-risk task.

## Implementation status

Implementation PR #1166 addresses only the confirmed core Docker executable PATH-resolution defect. It does not fix the legacy hackback runner, provide host attestation, remove Docker daemon privilege, or solve browser DNS rebinding. No merge or deployment has occurred.

## Validation and limitations

Source inspection only. Regression tests were added in PR #1166 but were not executed. No Docker daemon, rootless runtime, VM, hostile PATH, container escape, browser DNS rebinding, network firewall, or resource-exhaustion experiment was run. The findings describe the reviewed code paths and should not be generalized to uninspected deployment infrastructure.
