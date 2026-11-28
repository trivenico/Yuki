# Sandbox runtime attestation enforcement audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: runtime capability validation is not on the execution path

The sandbox runtime module defines `validate_runtime_capabilities(policy, capabilities)`. Its current implementation deliberately refuses all capability declarations because a trusted host-side runtime probe/attestation and verified mandatory-access-control enforcement are not implemented.

However, `run_sandboxed_task()` in `core/coding_task_runner.py` does not call this validator. Its public signature accepts a policy, image, workspace, command, and approval callback, but no runtime capability report or attestation. It calls `build_docker_argv()`, obtains approval, and starts Docker without first establishing that the host runtime satisfies the isolation contract.

`build_docker_argv()` also does not call `validate_runtime_capabilities()`. Consequently, the fail-closed validator is an isolated utility rather than an enforced prerequisite to execution. The current argv includes useful controls (non-root UID/GID, read-only container root, dropped Linux capabilities, no-new-privileges, resource bounds, and network=none), but it does not itself establish rootless daemon operation, required namespace isolation, seccomp profile coverage, mandatory access control, host-secret exclusion, or trusted cleanup guarantees.

The parser for Docker info explicitly labels its output diagnostic and untrusted; it cannot serve as the missing attestation.

## Impact and scope

This is a defense-in-depth and contract-enforcement gap in the sandbox execution API. A caller can reach Docker launch without a verified runtime isolation contract. It does not prove that a production host currently exposes this runner, that a sandbox was launched, or that a host compromise occurred.

This finding is distinct from the earlier incomplete approval payload: even a complete approval of the requested policy would not establish that the underlying runtime enforces the required isolation.

## Required remediation

1. Make runtime verification a mandatory dependency of the execution composition, not an optional diagnostic call.
2. Until a trusted verifier exists, keep the runner disabled and fail closed before approval and process launch.
3. Define a typed, host-originated attestation with provenance, freshness, runtime/daemon identity, effective security controls, and policy coverage; do not accept caller- or model-supplied booleans as proof.
4. Bind the verified runtime identity and attestation digest into the immutable execution plan and approval.
5. Revalidate the runtime identity immediately before launch, and reject daemon/context changes between verification and execution.
6. Add tests proving the runner never calls approval or starts Docker when verification is missing, invalid, stale, or no longer matches the selected daemon. Add positive-path tests only once a real verifier is implemented.

No runtime code changed, no Docker daemon was contacted, no sandbox was launched, and no local tests were run.
