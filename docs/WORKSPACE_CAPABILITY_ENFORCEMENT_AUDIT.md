# Workspace capability enforcement audit

**Date:** 2026-11-28  
**Scope:** `SandboxPolicy` and Docker argv construction.  
**Result:** source-only finding; no runtime changes and no sandbox execution.

## Finding: workspace is mounted even when read capability is absent

`core/sandbox_policy.py` defines `read_workspace` and `write_workspace` as distinct allowed capabilities. The default `SandboxPolicy` has an empty capability set. However, `core/sandbox_runtime.py::build_docker_argv` always adds a bind mount from the supplied host workspace to `/workspace`; it selects only the mount mode based on whether `write_workspace` is present. It never checks whether `read_workspace` is granted.

The tests currently encode this behavior: the default policy is expected to produce a read-only workspace mount, while a policy with `write_workspace` produces a read-write mount. Thus an empty capability set still grants workspace read access. This is inconsistent with the explicit capability model and least-privilege expectation that access is granted only when requested.

This is an authorization/policy enforcement gap, not a path traversal finding. The mount is read-only by default, and this review does not establish that the runner is deployed or has been used in production.

## Required remediation

1. Make workspace access conditional on explicit `read_workspace` or `write_workspace` capability; treat `write_workspace` as implying read access only if that implication is explicitly documented and validated.
2. Prefer no workspace mount when neither capability is present. If the command requires a working directory, use a private empty scratch mount instead.
3. Keep production/unattended execution restricted to a verified immutable input snapshot, mounted read-only. Do not combine this fix with re-enabling live workspace mounts.
4. Add policy/argv tests asserting that an empty capability set has no host workspace mount, `read_workspace` yields read-only access, and `write_workspace` is rejected or separately gated on privileged paths.
5. Bind the effective mount plan into the approval request so authorization covers the actual host resources exposed to the container.

**Disposition:** policy enforcement blocker. The default empty capability policy currently exposes workspace contents through a read-only bind mount; correct the capability-to-mount mapping before treating the policy as least-privilege.
