# Host execution-gate process identity

`core.execution_gate_identity` adds the host-side identity adapter used by
the Docker supervisor's `gate_identity_binder` callback.

`LinuxGateProcessIdentityResolver` reads the daemon-reported host PID under
host `/proc`, requires the exact configured gate `argv[0]`, reads all four UID
credentials from `/proc/<pid>/status`, and requires real/effective/saved/fs UIDs
to agree. It reads process start time before and after inspection to reject a
PID-reuse race. The returned effective UID is the host-visible UID expected by
Linux `SO_PEERCRED`; it is not guessed from the container's configured user.

`ExecutionGateIdentityBinder` checks the full daemon-assigned container ID
against the second Docker inspect result, verifies that the same PID is still
running, resolves its host UID, and binds the PID/UID exactly once to the
already-open `ExecutionGateAuthorizationListener`. It never delivers an
authorization. The supervisor remains responsible for invoking the separate
one-shot releaser only after all evidence checks pass.

Production release-provenance composition now requires both
`gate_identity_binder` and `gate_releaser` in
`build_docker_engine_supervisor_collector`. This prevents the factory's
default production path from silently omitting host peer-identity binding.
Explicit legacy mode remains an opt-out and is not execution-gate protected.

Example operator composition:

```python
from core.execution_gate_identity import (
    ExecutionGateIdentityBinder,
    LinuxGateProcessIdentityResolver,
)
from core.docker_engine_supervisor_factory import (
    build_docker_engine_supervisor_collector,
)

uid_resolver = LinuxGateProcessIdentityResolver(
    expected_argv0="/usr/local/bin/yuki-execution-gate",
)
identity_binder = ExecutionGateIdentityBinder(
    listener=prepared_gate_listener,
    uid_resolver=uid_resolver,
)
collector = build_docker_engine_supervisor_collector(
    api=docker_api,
    host_id_provider=host_id_provider,
    owner_hash_resolver=owner_hash_resolver,
    evidence_builder=host_evidence_builder,
    gate_identity_binder=identity_binder,
    gate_releaser=gate_releaser,
)
```

The configured `expected_argv0` must match the fixed entrypoint path inside
the pinned image. This is a deployment contract, not a value to accept from
task data. Procfs must be visible in the host PID namespace. The resolver
deliberately rejects credential transitions rather than guessing
user-namespace mappings. Rootless Docker and user-namespace-remapped
deployments must verify all four host-visible UID fields and private socket
permissions under the actual mapping before enabling this path.

The tests use a synthetic procfs tree and fake listener; they do not validate
real Docker, procfs namespace behavior, bind-mounted Unix socket permissions,
or rootless/userns mappings. No local test execution is claimed.