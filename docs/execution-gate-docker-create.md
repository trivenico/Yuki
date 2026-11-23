# Gate-first Docker create command

`core.execution_gate_docker.build_gate_container_create_argv` builds the
first half of the gate-first Docker lifecycle. It reuses the existing restrictive
sandbox policy builder, changes the Docker operation from `run` to
`create`, removes Docker's `--init` shim so the image-owned gate
is PID 1, pins the entrypoint to
`/usr/local/bin/yuki-execution-gate`, and mounts the operator-provisioned
private gate directory at `/run/yuki-gate`. The image receives only the
gate's identity/timeout arguments; it receives no task command. The exact task
argv remains inside the signed one-shot host authorization envelope.

The builder requires the existing complete Yuki ownership-label set, an
immutable digest-pinned image, a generated Yuki container name, a non-symlink
Docker executable, and a non-symlink executor-owned private directory that is
not group/world writable. The same workspace, network, read-only root,
capability-drop, resource-limit and security policy flags are retained from
`build_docker_argv`.

This is deliberately only a command-construction primitive. It does not invoke
Docker, start the container, bind the listener, collect evidence, authorize the
gate, stream bounded task output, or clean up. The existing
`DockerSandboxExecutor` remains unchanged and still uses its legacy direct
`docker run` path. Do not treat this builder as production activation
or route task execution through it until the complete create → inspect → start
gate → inspect/evidence → bind → release → monitor → stop/remove lifecycle is
implemented.

The private directory must be provisioned so the gate process can connect to
the host listener under the actual rootless/user-namespace UID mapping while
remaining inaccessible to task-controlled processes. Current checks establish
host-side ownership and writable-bit restrictions only; they do not prove
container-side socket access. Rootless Docker, userns-remap, socket mount
semantics, PID identity and end-to-end failure cleanup remain integration
requirements.

Tests cover argv construction and rejection cases using a temporary directory
and fake paths. They do not invoke Docker or prove runtime enforcement. Tests
have not been executed in this environment.
