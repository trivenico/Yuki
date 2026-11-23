# Yuki Go worker-health monitor

This optional Go process polls Yuki's existing administrator-only
`GET /v1/admin/workers` endpoint. It is telemetry-only: it does not claim
tasks, renew leases, mutate worker records, restart workers, or make approval
decisions. Python remains the task and authorization authority.

## Run

Requires Go 1.22 or newer.

```sh
cd go/worker-health
YUKI_API_BASE_URL=https://yuki.example.com \
YUKI_ADMIN_TOKEN='<short-lived-admin-session-token>' \
go run ./cmd/yuki-worker-health
```

Set `YUKI_WORKER_HEALTH_INTERVAL` to 5..300 seconds (default 15). HTTP is
accepted only for localhost/loopback development. HTTPS is mandatory otherwise.
Redirects are not followed, bearer tokens are never logged, response bodies are
limited to 1 MiB, and each request has a five-second deadline.

The monitor emits metadata-only JSON lines with counts and UTC timestamps. It
never emits worker metadata or credentials. A stale worker count is reported
as an error on stderr; the monitor continues polling. SIGINT/SIGTERM stops it.

## Verify

```sh
go test -race ./...
go vet ./...
go build ./cmd/yuki-worker-health
```

The monitor requires an authenticated administrator session. Use a dedicated,
short-lived credential and rotate it according to the deployment's session
policy. Do not embed tokens in source files or container images.
