# Linux production web service runner

This service runner is the Linux process boundary for a deployment-composed Yuki ASGI application. It is intentionally separate from `main.py`, the interactive `yuki` CLI, and the local Docker terminal image.

## What the runner guarantees

- Requires `YUKI_ENVIRONMENT=production`.
- Binds only to loopback. A private reverse proxy may connect to it locally; the runner refuses `0.0.0.0` and non-loopback listener addresses.
- Loads one trusted `module:callable` bootstrap from `YUKI_WEB_BOOTSTRAP`.
- Requires the factory to return an ASGI-callable runtime exposing an owned `ProductionSecurityRuntime` and an idempotent `close()`.
- Verifies the complete production security runtime before opening a listener.
- Runs one Uvicorn worker with ASGI lifespan enabled; Uvicorn handles SIGTERM and graceful shutdown.
- Closes the runtime if startup validation fails or the server exits, including exceptional exits.
- Disables Uvicorn proxy-header trust and server-identification header by default. Configure trusted proxy handling separately only after the proxy boundary is designed and tested.

It does **not** create identities, policy engines, database credentials, audit anchors, or secrets. It does not run migrations. It does not construct an application by calling the stock `scripts.run_web.create_app()` helper.

## Deployment bootstrap contract

The deployment-owned bootstrap module is installed outside the application checkout, is root-owned and not writable by the `yuki` service account, and exports a zero-argument callable:

```python
def create_runtime():
    # Resolve short-lived secrets using the instance's least-privilege identity.
    # Construct trusted human/workload identity resolvers and policy.
    # Construct the independently administered audit-anchor adapter.
    # Construct ProductionSecurityRuntime against the production PostgreSQL
    # database, after the deployment migration step has completed.
    # Compose the application against production-grade persistence and auth.
    # Return one ASGI runtime that owns these dependencies and exposes:
    #   production_security_runtime
    #   close()
    # The ASGI runtime must verify readiness during lifespan startup and expose
    # the private /health/ready endpoint.
    ...
```

The factory must return the actual owned runtime, not a bare `ProductionSecurityContext`, a test double, or a local SQLite composition. The application persistence and authentication layers must be production-appropriate too; satisfying the security-runtime type check alone does not certify every application store. The current stock `scripts.run_web.create_app()` composes local SQLite-backed stores and is not this deployment bootstrap.

The runner's preflight calls `production_security_runtime.verify_readiness()` before binding. The ASGI lifespan must perform its own readiness check as well. If any required dependency is unavailable, the service must fail startup and remain unavailable.

## Host installation outline

1. Install the pinned Yuki release in a versioned directory and create a dedicated virtual environment at the stable path used by the unit.
2. Install the reviewed web dependency set (currently `requirements-web.txt`) into that environment.
3. Install the deployment-owned bootstrap package in a separate root-owned directory, for example `/opt/yuki/host-bootstrap`.
4. Install `yuki-web.service.example` as `/etc/systemd/system/yuki-web.service`; review every path and account before enabling it.
5. Create `/etc/yuki/web.env` owned by `root:yuki`, mode `0640`, containing only non-secret settings such as:
   - `PYTHONPATH=/opt/yuki/host-bootstrap`
   - `YUKI_WEB_BOOTSTRAP=yuki_host_bootstrap:create_runtime`
6. Ensure the bootstrap retrieves secrets at runtime from OCI Vault using a narrowly scoped instance principal. Do not put secrets, passwords, database URLs, signing keys, or tokens in the environment file.
7. Ensure `/var/lib/yuki` is the only persistent application write area. Configure the trusted bootstrap/application storage paths accordingly.
8. Run migrations through a separately approved, single deployment job using the reviewed migration manifest. The service account must not have migration-owner privileges.
9. Keep the reverse proxy and listener private; the first pilot should be reachable only over Bastion/VPN. Do not open the application port publicly.
10. Verify service startup failure with missing PostgreSQL, policy, identity, durable audit, and independent checkpoint dependencies before any pilot traffic.

## Example operator checks

```sh
sudo systemd-analyze verify /etc/systemd/system/yuki-web.service
sudo systemctl daemon-reload
sudo systemctl start yuki-web.service
sudo systemctl status yuki-web.service --no-pager
sudo journalctl -u yuki-web.service -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health/ready
```

These commands are an operator runbook, not evidence that an OCI host has been configured or tested. The readiness endpoint must return HTTP 200 only after all required production dependencies and the independent audit checkpoint have been verified.

## Explicit limitations

- No production bootstrap module exists in this repository yet; the example import path above is a deployment-owned integration contract, not a working default.
- No PostgreSQL service, identity provider, policy engine, OCI Vault policy, external audit-anchor service, reverse proxy, or OCI host is provisioned by these files.
- No migration is run by this service.
- This unit is a reviewed starting point. Validate its systemd directives, actual writable paths, resource limits, and network policy on the selected OCI Linux image before installation.
- Windows service packaging is a later milestone after Linux pilot acceptance.
