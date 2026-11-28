# Sandbox workspace mount path TOCTOU audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: workspace validation is separated from Docker's path resolution

`core/sandbox_runtime.py::build_docker_argv` checks the supplied workspace with path-based operations (`is_symlink`, `exists`, `is_dir`), resolves it, and serializes the resolved pathname into Docker's `--mount type=bind,src=...` argument.

`core/coding_task_runner.py::run_sandboxed_task` builds that argv before invoking the approval callback, then starts Docker after approval. The request binds `os.path.realpath(workspace)`, but neither the approval request nor the Docker mount binds a stable directory identity (for example, a verified device/inode or an immutable staged snapshot identity). The Docker daemon resolves the source pathname when it processes the container-create request, not when the Python path checks ran.

A local actor able to rename or replace the workspace path between validation/approval and daemon-side mount resolution could cause Docker to mount a different directory, including a symlink target, than the one checked. The source establishes the check/use gap; exploitability depends on whether an untrusted actor can mutate the relevant parent directory during that interval. This is distinct from the already documented broader use of a live, mutable workspace rather than an immutable snapshot.

## Impact

The effective host directory exposed to the container may differ from the directory that passed validation and was presented for approval. Depending on the replacement target and granted mount mode, this can expose unintended host data or permit writes to an unintended directory.

## Required remediation

1. Execute only from a securely staged, immutable workspace snapshot whose identity is bound into approval.
2. Ensure untrusted principals cannot mutate the staging parent or replace the staged path before the daemon resolves the bind mount.
3. Where supported, use a runtime interface that can bind a verified directory handle or otherwise attest the mounted source identity; do not treat a pre-launch pathname check as identity binding.
4. Add race tests that replace/rename the workspace and parent path between validation, approval, and launch; assert that no alternate directory is mounted.
5. Keep mount mode and exact staged snapshot identity in the approved execution plan.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
