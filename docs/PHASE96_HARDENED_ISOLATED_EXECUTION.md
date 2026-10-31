# Phase 96 — hardened isolated coding-task execution

## Existing foundation

Yuki already had a Docker-backed coding-task runner (Phases 74–75), fail-closed sandbox policy, per-run trusted approval callback, resource limits, bounded output collection, explicit container cleanup, and hash-only execution evidence. This phase hardens that existing implementation rather than creating a duplicate executor.

## Changes

- Coding-task execution now rejects mutable image tags and requires an immutable lowercase OCI/Docker reference ending in `@sha256:` plus 64 hexadecimal characters. Approval is not requested and Docker is not invoked for a mutable or malformed reference.
- Docker containers use `--init` to provide PID 1 signal forwarding and child-process reaping.
- Docker containers use `--ipc=private` to avoid sharing the host/default IPC namespace.
- Regression tests cover the immutable image requirement and mandatory isolation flags.
- Existing controls remain: no network, read-only root, non-root UID, dropped Linux capabilities, no-new-privileges, CPU/memory/PID limits, bounded output, execution timeout, and explicit cleanup.

## Operational requirements

- Deployments must configure a trusted image digest from a reviewed image supply chain; tests use synthetic digests and do not establish that an image exists or is safe.
- The Docker daemon and host kernel remain part of the trusted computing base.
- The current Docker argv builder rejects hosts without Unix UID/GID support (including native Windows execution); Windows desktop support needs a separately verified isolation backend such as a configured Linux VM/WSL2 Docker engine, not a fallback to host execution.
- Network allowlisting remains disabled until a separately enforced egress gateway exists.
- This runner is not yet the universal execution backend for all skills, plugins, desktop automation, and specialist agents.
- Container image scanning/signing, daemon hardening, external cleanup monitoring, and real-Docker adversarial integration tests remain release requirements.

## Verification

    python -m unittest tests.test_coding_task_runner -v
    python -m unittest tests.test_coding_task_execution -v
    python -m unittest tests.test_sandbox_runtime -v
    python -m unittest tests.test_sandbox_policy -v

Do not classify this as production-ready until exact-head Linux/Windows CI, image provenance checks, and deployment-level sandbox validation pass.
