# Per-execution gate authorization session

`ExecutionGateAuthorizationSession` composes the transport, host identity binder, signed authorization protocol, and host-observed exec transition into one fail-closed per-execution object.

## Intended host composition

1. Provision a fresh service-owned private directory for one execution. It must be owned by the non-root worker service and have mode 0700 or stricter.
2. Construct one session with immutable task, worker, spec, digest-pinned image, trusted host UID, host procfs UID resolver, and `LinuxGateExecTransitionObserver`.
3. Enter/open the session **before** creating the Docker container.
4. Pass `session.private_gate_directory` and `session.gate_arguments` to `build_gate_container_create_argv`. Use these startup arguments verbatim.
5. Create and start the gate-only container using `DockerGateContainerClient`. Supply the daemon-assigned full container ID to the Docker supervisor collector.
6. Inject `session.bind_gate_identity` as the collector identity binder and `session.release` as its gate releaser. The collector remains responsible for daemon inspect, ownership labels, immutable image identity, resource-policy checks, release-record publication, host evidence and second-inspect PID stability.
7. The session signs a fresh authorization only after the collector invokes the releaser. It sends the token/key over the one-shot listener, waits for the gate cryptographic-verification ACK, then requires the same host PID to exec the exact authorized argv before returning success.
8. Always close the session and remove the container in the caller's `finally` path. Any failure must prevent task success and trigger forced cleanup.

## Fail-closed semantics

- Identity binding is one-shot and must match the second Docker inspect.
- Release fields must match the session task, worker, spec and image.
- Authorization binds the full daemon container ID and exact argv.
- The session cannot retry a release after transport failure or uncertain acknowledgement.
- A transport ACK means authorization verification only. Success requires host procfs to observe the same PID, stable start time and UID executing exact argv.
- An unobserved or short-lived task is reported as unconfirmed, not started.
- The key reference is dropped before transport; the gate wipes its mutable key buffer after verification. Python cannot guarantee zeroization of immutable `bytes` objects; stronger zeroization requires a dedicated secret-handling implementation.
- This session does not implement task output streaming, exit-code collection, lease-loss cancellation, post-exec timeout enforcement, or container cleanup. The host launcher must provide those bounded lifecycle responsibilities.

## Remaining integration and validation

This is a composition primitive, not a production-enabled executor. A concrete worker-service factory must provision the private directory, construct the session, create/start the gate-only container, inject callbacks into the trusted supervisor collector, monitor task completion/cancellation, and guarantee container removal. Existing direct `DockerSandboxExecutor` remains unchanged and must not be used as a fallback.

Docker bind-mount access and `SO_PEERCRED` UID/PID behavior under rootless Docker and user namespaces remain unvalidated. The gate `--host-uid` must match the UID observed by the container process for the host-side Unix socket peer. This requires real integration testing on each supported daemon configuration before enablement.
