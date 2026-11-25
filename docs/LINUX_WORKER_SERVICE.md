# Linux production worker service

The Python distribution exposes `yuki-worker` from the packaged
`automation.worker_service` module. The older `python -m scripts.run_worker`
path remains as a source-tree compatibility wrapper; `scripts/` is not part
of the installed wheel.

## Trust boundary and bootstrap

The worker is deliberately inert unless
`YUKI_ENABLE_DISTRIBUTED_WORKERS=true` is explicitly set. It also requires
`YUKI_WORKER_BOOTSTRAP=module:callable`. That deployment-owned factory must
return a ready `ProductionWorkerRuntime` that owns a real
`ProductionSecurityRuntime`. It must resolve trusted worker/service identities,
verify workload credentials, use the production policy gateway, and connect to
the same PostgreSQL database as the security lease runtime. It must not accept
identity or policy claims from task data, model output, YAML, or user-controlled
environment values.

The application does not provide a fake/default bootstrap, create credentials,
run database migrations, or initialize an audit anchor. The factory must obtain
secrets from OCI Vault or an equivalent host secret manager, and fail closed if
identity, policy, PostgreSQL, durable audit, or the independent audit checkpoint
is unavailable. Database migrations remain a separate deployment operation.

## systemd

Review and adapt `deploy/systemd/yuki-worker.service.example` for the host,
then install it as `/etc/systemd/system/yuki-worker.service`. Create the
`yuki` service account and the two writable directories before starting it.
Keep the virtual environment and application code root-owned and read-only to
the service account. Restrict network egress at the host firewall to only the
required PostgreSQL, identity, policy, audit-anchor, and approved provider
endpoints. Do not expose the worker listener publicly; this process has no
inbound listener.

The unit enables systemd filesystem/process hardening and an empty capability
set. Confirm that the selected host paths and Python runtime work with those
controls before rollout. Logs go to journald. Persistent task/application data
belongs on encrypted durable storage, not in the release directory.

## Operational limits

This is a worker process foundation, not a complete Yuki conversational API,
desktop service, or proof of production readiness. It does not provision OCI,
create a bootstrap integration, migrate PostgreSQL, publish audit checkpoints,
or deploy itself. Before enabling it, complete the production bootstrap,
secret-manager integration, migration executor, monitoring/alerts, backup and
restore drill, release signature/provenance verification, and rollback plan.
Start in a private staging environment only. Windows service packaging remains
a separate later acceptance target.
