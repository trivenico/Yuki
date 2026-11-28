# Docker daemon identity and environment stability audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: Docker endpoint selection is re-read independently at launch and cleanup

`core/coding_task_runner.py` obtains the Docker CLI environment by calling `_docker_environment()`. That helper copies a selected set of connection variables from the process environment, including `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, TLS settings and certificate path.

The runner calls this helper only when it invokes `subprocess.Popen()`, after the approval callback has returned. The approval request contains image, resolved workspace, command and selected resource limits, but no Docker endpoint/context identity or immutable environment digest.

The cleanup helper `_kill_container()` calls `_docker_environment()` again, independently. It can therefore target a different endpoint/context from the one used by the launch if process environment or Docker context selection changes between those calls. A cleanup success on another daemon would not establish removal of the original container; a cleanup failure could leave the original container running or retained.

The source establishes the repeated environment reads and missing endpoint binding. It does not establish that a production caller currently mutates these variables concurrently or that a container has been orphaned.

## Impact and scope

This is a daemon-identity consistency and cleanup-assurance gap. It weakens the runner's claim that it owns and confirms cleanup of the exact container it launched. It also leaves approval and any future runtime attestation unbound to the Docker daemon actually selected at process start.

## Required remediation

1. Resolve Docker CLI path, context/endpoint and TLS/config settings once at the trusted execution boundary.
2. Establish and record a stable daemon identity using a trusted host-side verifier; do not treat a context name or environment-variable digest as daemon attestation.
3. Bind the daemon identity and execution endpoint selection into the immutable approved execution plan.
4. Use the same captured, immutable connection configuration for launch, inspection and cleanup; never re-read mutable process environment during cleanup.
5. Confirm the created container ID and daemon identity, then remove that exact container on the same verified daemon. Treat endpoint mismatch or unconfirmed removal as an uncertain cleanup outcome requiring reconciliation.
6. Add tests that mutate `os.environ` and Docker context selection after approval and between launch and cleanup, asserting that launch and cleanup cannot silently target different daemons.

No runtime code changed, no Docker daemon was contacted, no container was launched, and no local tests were run.
