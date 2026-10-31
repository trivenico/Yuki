# Yuki local desktop API

## Purpose

`core/local_api.py` exposes a small WSGI interface so a future desktop shell can call the exact same `YukiApplicationRuntime` used by the CLI. It is a local IPC bridge, not the Oracle Cloud sync API.

## Routes

All routes require a bearer token.

| Method | Route | Purpose |
|---|---|---|
| GET | /v1/health | Local service health |
| GET | /v1/history | Active conversation ID and messages |
| POST | /v1/chat | Send one message through the shared runtime |
| POST | /v1/conversations | Create and select a conversation |

The API limits request bodies to 64 KiB, rejects extra JSON fields, emits no CORS headers, checks the connecting peer is loopback, and serializes runtime access to protect the single active conversation. Browser origins are denied unless their exact HTTP(S) origin is allow-listed. The API has no tool-execution route and does not expose provider credentials.

## Host requirements

The hosting server MUST bind only to `127.0.0.1` (or `::1`) and MUST NOT be placed behind a public reverse proxy. Generate a cryptographically random token of at least 32 characters and pass it to `LocalAPI` from a trusted host process. Do not put the token in renderer JavaScript, URLs, command-line arguments, logs, sync events, or source control. The desktop main process should retrieve it from an OS credential vault and proxy renderer requests through a narrow IPC bridge.

Do not use wildcard origins. Prefer no browser Origin at all by calling from the desktop main process. If a development origin is needed, allow-list its exact origin and keep developer tools disabled in release builds.

## Example host composition

The following is a minimal development-only example. The server must be loopback-bound and the token must be loaded from a secure host-side secret source.

```python
from wsgiref.simple_server import make_server
from core.application_runtime import YukiApplicationRuntime
from core.local_api import LocalAPI

runtime = YukiApplicationRuntime.create()
app = LocalAPI(runtime, token=load_local_api_secret())

try:
    with make_server("127.0.0.1", 43127, app) as server:
        server.serve_forever()
finally:
    runtime.close()
```

This example is not a production launcher: it does not implement OS credential provisioning, process supervision, graceful request cancellation, TLS (not needed for loopback transport but still requires host threat review), or Windows firewall/port lifecycle controls. Do not copy the placeholder `load_local_api_secret()` literally.

## Current limitations

No desktop shell, renderer/main-process bridge, secure token provisioning, service auto-start, streaming, cancellation, multi-window session isolation or installer is included. This API currently serializes calls into one active conversation. The API is intentionally small and must not grow arbitrary filesystem, shell, browser or tool-execution endpoints without separate threat modeling and explicit approvals.
