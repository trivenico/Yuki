# Audit 075 — Agent Tool Permissions, Privilege Escalation & Host-System Isolation

**Scope:** skill/plugin import and invocation, tool dispatch, host subprocess execution, Docker sandbox policy, runtime verification, workspace mounts, and gate-first execution.

## Reviewed source
- core/sandbox_policy.py
- core/sandbox_runtime.py
- core/coding_task_runner.py
- core/coding_task_execution.py
- core/guarded_skill_runtime.py
- core/guarded_tool_runtime.py
- core/permissions.py
- core/plugin_loader.py
- core/approved_skill_runtime.py
- core/gate_first_sandbox_executor.py
- core/agent.py
- hackback/sandbox.py
- Related sandbox, coding-task, plugin-loader, and agent tests

## Positive controls observed
- SandboxPolicy validates immutable capabilities, bounded resource limits, read-only root, non-root execution, and explicit network mode.
- Docker argv uses a digest-pinned image, non-root UID, read-only root, dropped Linux capabilities, no-new-privileges, bounded memory/CPU/PIDs, no network by default, and read-only workspace unless write_workspace is granted.
- Allowlisted network mode fails closed without a separately enforced egress gateway.
- Sandbox runtime explicitly refuses to treat Docker self-reported metadata or caller-supplied booleans as trusted isolation attestation.
- Coding task runner uses argv (shell=False), requires a per-run approval callback bound to exact execution intent, caps output and duration, and requires container cleanup confirmation.
- Skill/plugin discovery is disabled by default and requires an explicit trusted module list. Documentation correctly states Python imports execute with Yuki's privileges and are not a sandbox.
- Guarded skill invocation binds one-shot approval to exact arguments and can require the same registered skill object at dispatch.
- Gate-first Docker execution validates a host preflight and has no direct-command fallback.

## Findings

### P1 — Host Docker executable was selected through inherited PATH (fixed in companion PR)
The coding-task runner launched the host Docker CLI using the bare executable name docker. Its cleanup helper did the same. subprocess therefore resolved the executable through inherited PATH. If an attacker-controlled or writable directory appeared earlier in PATH, a sandbox execution request could run a substituted executable on the host before Docker isolation was applied. Container hardening does not protect the host-side launcher.

**Fix submitted:** add a deployment-configured absolute docker_binary path (default /usr/bin/docker), resolve it once, and use that exact path for both container launch and cleanup. Relative executable names are rejected before approval or process launch. Added regression tests for relative-path rejection and configured absolute-path use.

### P1 — Docker daemon access remains a host-level trust boundary
The coding-task runner inherits selected Docker connection settings (DOCKER_HOST, DOCKER_CONTEXT, TLS settings and DOCKER_CONFIG). Access to a privileged Docker daemon can amount to host-level control. Container flags do not compensate for an untrusted daemon endpoint, Docker socket, client config, TLS material, or compromised daemon.

**Recommendation:** keep Docker socket and client configuration service-owned and inaccessible to agents/skills/workspaces. Use a dedicated rootless daemon or isolated worker host; do not mount the Docker socket into agent containers. Pin daemon endpoint and TLS trust in deployment configuration and verify ownership/permissions during startup.

### P1 — General Python skills and plugins execute in the Yuki host process
The guarded skill runtime explicitly says it does not sandbox skill code. PluginLoader imports trusted modules into the process; Python module import executes code with Yuki's privileges. Allow-listing modules and approval gating provide governance but not isolation after import.

**Recommendation:** treat plugin code as trusted first-party code. For third-party or model-generated skills, execute through a separate worker process/container with a narrow RPC contract, dedicated identity, no inherited secrets, read-only code, explicit filesystem mounts, and network egress controls.

### P1 — Sandbox runtime isolation is not attested by the current capability declarations
SandboxRuntimeCapabilities values are caller-supplied diagnostics, not host proof. validate_runtime_capabilities intentionally refuses to promote those values to attestation without a trusted host-side verifier. Docker info parsing also explicitly lists controls it cannot establish, including namespace enforcement, per-container seccomp profile, network egress, secret exclusion, hard resource limits, and ephemeral cleanup.

**Recommendation:** do not enable untrusted-code execution in production until host preflight independently verifies effective container settings and a controlled probe. Keep gate-first mode fail-closed; deploy workers on disposable, unprivileged hosts with verified namespaces, seccomp, AppArmor/SELinux, cgroups, mounts, egress and cleanup.

### P2 — Workspace write capability deliberately grants shared workspace mutation
build_docker_argv mounts the workspace read-only by default and only grants read-write when write_workspace is explicit. This is least-privilege at the mount level, but an approved writable workspace is still a shared mutable resource and can contain symlinks or build hooks consumed later by trusted host processes.

**Recommendation:** use per-task disposable workspaces, avoid running host-side build hooks on untrusted output, validate patch paths/symlinks, and promote artifacts only after review.

## Remediation status
- **Implemented in companion PR #1137:** pin host Docker executable path for launch and cleanup; add regression tests.
- No blanket plugin isolation claim is made. Python in-process plugins are trusted code until moved behind a process/container boundary.
- Docker daemon trust, host attestation, and deployment egress/isolation controls remain operator-level requirements.

## Validation status
- Source inspection completed for the listed execution, plugin, permission, and sandbox paths.
- Regression tests added but **not executed**.
- No Docker daemon, rootless runtime, Linux namespaces, seccomp/AppArmor profile, malicious plugin, or host privilege-escalation simulation was run.
- Implementation PR remains open and unmerged; no deployment claim is made.