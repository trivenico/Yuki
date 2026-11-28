# Workspace snapshot execution adoption audit

**Date:** 2026-11-28  
**Scope:** source review of the ordinary Docker coding-task runner and the immutable workspace staging contract/prototype.  
**Result:** the reviewed runner path does not consume the immutable staging primitive. No runtime behavior was changed and no sandbox was launched.

## Finding: Docker coding-task runner mounts the live workspace, not a reviewed snapshot

`core/coding_task_runner.py::run_sandboxed_task` accepts a caller-provided workspace path and passes it directly to `build_docker_argv`. The approval request binds `os.path.realpath(workspace)`, but the path remains the live workspace. `core/sandbox_runtime.py::build_docker_argv` resolves and mounts that directory at `/workspace`. The mount is read-only by default, but becomes read-write when the policy includes `write_workspace`.

The immutable staging prototype in `core/immutable_workspace_staging.py` is not imported or invoked by this runner. Its design contract requires a task-specific allowlisted snapshot and explicitly prohibits mounting the owner's live workspace. Consequently, the existing Docker runner does not inherit the prototype's per-file checks, staged manifest, or attempt-specific snapshot identity.

A read-only bind mount is not a point-in-time snapshot: host-side edits can change files visible to the container during execution, and unreviewed files already present in the workspace are exposed. With `write_workspace`, sandboxed code can modify the live workspace directly. The approval callback's binding to the resolved path does not bind approval to immutable source bytes or a reviewed tree digest.

This is an execution-boundary and review-integrity gap. The source does not establish that this runner is enabled in a production host or that any task has been executed through it. Do not describe it as a demonstrated compromise or claim the staging prototype is deployed.

## Required remediation

1. Make the immutable snapshot artifact—not a caller-supplied workspace path—the only workspace input accepted by the production runner.
2. Bind approval to task/attempt identity, reviewed Git tree and patch digest, canonical staging manifest digest, snapshot identity, image digest, argv, mount plan and resource/network policy.
3. Mount the snapshot read-only and verify its identity immediately before container creation. Do not offer a live-workspace read-write option on the privileged/unattended path.
4. Keep writable scratch/output separate from the input snapshot. Validate and import selected outputs through a separate descriptor-relative review flow; never write sandbox output directly into the owner's workspace.
5. Fail closed if snapshot creation, manifest verification, launch-time identity verification or output separation is unavailable.
6. Add integration tests proving the runner rejects a live workspace path, launches only from a staged snapshot, binds approval to the snapshot manifest digest, and cannot write to the input mount. Add a separate test proving output import cannot overwrite unapproved workspace paths.

## Evidence reviewed

- `core/coding_task_runner.py`: caller-provided workspace is passed to the Docker argv builder; approval binds the resolved path, not source content identity.
- `core/sandbox_runtime.py`: workspace directory is mounted at `/workspace`; access mode depends on `write_workspace` capability.
- `core/immutable_workspace_staging.py` and `docs/IMMUTABLE_CODING_WORKSPACE_STAGING.md`: isolated staging primitive and contract exist, but the ordinary runner does not consume them.

**Disposition:** integration blocker for production/unattended coding execution. Keep such execution disabled until the runner accepts only verified staged snapshots and the output path is separately constrained.
