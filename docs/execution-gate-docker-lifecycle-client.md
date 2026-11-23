# Docker gate container control-plane client

`DockerGateContainerClient` is a narrow control-plane primitive for the gate-first executor path. It accepts only a create argv with the fixed image-owned `/usr/local/bin/yuki-execution-gate` entrypoint and an exact generated Yuki container name. It never accepts a task command.

The client performs `docker create`, requires exactly one full 64-character daemon-assigned container ID, and starts by that ID rather than by a mutable name. Docker calls use an explicit local Unix-socket endpoint, a minimal environment, closed file descriptors, and a bounded command timeout.

Create failure, malformed create output, create timeout, and start failure trigger best-effort `docker rm -f` reconciliation by the unique generated container name. Cleanup is considered successful only when Docker confirms removal or explicitly reports that the container is already absent. Permission, daemon, transport, and timeout errors remain failures.

This is not the complete gate-first executor. It does not provision or mount the private listener, inspect/verify container labels and image identity, collect live host evidence, bind the gate PID/UID, release authorization, monitor exec/task output, handle task lease loss, or coordinate final cleanup around a running task. The existing `DockerSandboxExecutor` remains unchanged. Do not enable production gate-first execution until those stages are composed and integration-tested, including rootless/user-namespace mount and peer-credential behavior.


## Bounded subprocess output

The default Docker CLI runner now drains stdout and stderr concurrently while
retaining at most 64 KiB per stream. If either stream exceeds its ceiling, the
command fails closed after the pipes are drained. A bounded command timeout
kills the Docker CLI process group. Tests can still inject a deterministic
runner; production composition uses the bounded subprocess runner by default.

The client remains a create/start/remove primitive. It does not independently
inspect the daemon-assigned container, collect release evidence, bind the gate
PID/UID, release authorization, or monitor task execution. Those phases must
be composed by the separate gate-first supervisor before this path can be
enabled in production.
