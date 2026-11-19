# Phase 99 — service health and readiness

## HTTP probes

The web shell exposes two unauthenticated, low-information operational endpoints:

- GET /healthz is a liveness probe. It confirms that the ASGI process can answer HTTP and does not claim that databases, providers, or external dependencies are healthy.
- GET /readyz is a readiness probe. It returns HTTP 503 until the ASGI lifespan startup has completed, including durable sandbox recovery when enabled. It returns HTTP 503 again after runtime shutdown begins.

Both endpoints return only a fixed service identifier and status. They do not disclose configuration, database paths, provider names, exception details, or secrets. HEAD requests are supported.

## Deployment guidance

Configure the container orchestrator or reverse proxy to use /healthz for liveness and /readyz for traffic admission. Do not restart a process solely because /readyz is temporarily 503 during startup or shutdown. The readiness endpoint deliberately does not run database queries on every probe; database integrity and dependency diagnostics belong in controlled startup checks and operational monitoring.

## Verification

tests/test_web_readiness.py covers the not-ready 503 response, ready 200 response, and independence of liveness from readiness. The test has been added but not independently executed here. CI results must be checked on the current branch head before claiming a pass.