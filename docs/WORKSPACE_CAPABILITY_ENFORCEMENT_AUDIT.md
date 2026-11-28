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

## Additional finding: approval payload omits effective policy and mount privileges

`SandboxExecutionRequest` includes image, resolved workspace path, command,
timeout, memory, CPU and output limit. It omits the policy capability set,
network mode, maximum process count, and the effective mount plan. The runner
builds Docker argv from the full policy before invoking approval, but the
approval callback receives only the reduced request object.

This creates a material mismatch between what the approval system can inspect
and what is launched. For example, a policy containing `write_workspace`
causes `build_docker_argv` to create a read-write bind mount, while the
approval request contains no field indicating that write access was granted.
The callback cannot independently bind consent to that privilege from the
request it receives. Similarly, `max_processes` affects the Docker
`--pids-limit` but is absent from the approval payload.

This is an approval-integrity gap. It is distinct from the missing
`read_workspace` enforcement above: even after mount capability enforcement
is corrected, the approval record must describe the effective access plan.

### Remediation

1. Extend the immutable approval request with the exact capability set,
   network mode, process limit, all resource limits, and a canonical effective
   mount plan (source identity, destination, access mode, and scratch/output
   mounts).
2. Prefer an immutable execution-plan object as the single input to both
   approval and argv generation; do not independently reconstruct policy
   semantics in the approval adapter and runtime builder.
3. Bind snapshot/manifest digest, container image digest, argv, runtime
   identity and output-import policy into the same approved plan.
4. Add tests proving that changing any privilege-bearing field changes the
   approval request and that read-write access cannot launch after approval
   of a read-only plan.

