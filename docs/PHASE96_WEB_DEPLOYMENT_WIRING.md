# Phase 96 — web deployment entrypoint wiring

## What changed

The checked-in authenticated web launcher, `scripts/run_web.py`, now calls
`api.production_runtime.build_production_conversation_runtime` instead of
calling the lower-level API builder directly.

This makes the production composition boundary real for the web service:
when `sandbox_jobs_enabled` is false, startup remains on the ordinary API
path and does not initialize Docker or the durable sandbox databases. When it
is true, the production factory composes the durable job store, Docker runtime,
recovery audit adapter, and recovery coordinator. The ASGI lifespan hook then
runs recovery before startup completes and fails startup on any unresolved
recovery condition.

The web launcher continues to expose no model-callable tools by default. Its
provider, authentication, inference, and income-opportunity dependencies are
constructed as before; only the final API runtime composition call changes.

## Run the web service

Install the optional web server and launch the checked-in web entrypoint:

```bash
python -m pip install -r requirements.txt -r requirements-web.txt
python scripts/run_web.py
```

The launcher binds to loopback by default. Public binding remains explicitly
opt-in and is not a substitute for TLS termination, trusted-host validation,
rate limiting, request logging, or deployment network policy.

## Durable sandbox jobs

Durable jobs remain disabled by default. To enable them, configure the
independent `sandbox_jobs_*` settings documented in
`PHASE95_SANDBOX_PRODUCTION_COMPOSITION.md`, provision the jobs root as a
service-owned mode-0700 directory, and ensure the Docker executable and
digest-pinned image are available to the service.

The web launcher is the API deployment path that enforces recovery composition.
The `main.py` interactive CLI and existing `docker/Dockerfile` continue to
start the guarded terminal runtime; they do not enable durable API sandbox
jobs. Do not change the CLI container's command to the web launcher without
also installing `requirements-web.txt` and applying the web deployment's
secret, storage, network, and reverse-proxy configuration.

## Verification

`tests/test_web_launcher.py` now asserts that `create_app()` invokes the
production runtime factory with the configured durable-job flag. The Phase 95
composition tests cover enabled/disabled dependency construction and
configuration validation.

The tests have been added but have not been independently executed in this
environment. GitHub Actions is the verification source for this branch; do
not treat queued or in-progress workflows as passing.
