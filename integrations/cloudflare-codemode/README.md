# Optional Cloudflare Code Mode adapter

This package is an **optional isolated execution backend**. It is not imported by
Yuki's Python core and does not own identity, task state, approvals, permissions,
or the authoritative audit log. Local/native execution remains independent of
Cloudflare.

## Runtime boundary

- Runs generated JavaScript with `DynamicWorkerExecutor` and a Worker Loader.
- Sets `globalOutbound: null`; generated code has no direct outbound network.
- Exposes only the exact `namespace.method` tool names in the immutable Yuki
  request manifest. Tool calls are proxied to the native control plane.
- Enforces source, tool count, call count, JSON argument/result, output, and
  timeout bounds before returning results.
- Never accepts identity or permissions from generated code.
- Does not expose shell, filesystem, arbitrary fetch, Cloudflare account APIs,
  secrets, or mutating tools by default.

## Required trusted binding

The Worker requires two bindings supplied by deployment configuration:

- `LOADER`: Cloudflare Worker Loader binding used by Dynamic Workers.
- `CONTROL_PLANE`: service binding to a trusted Yuki control-plane Worker.

The service binding must implement these internal endpoints. These are an
adapter contract, not a replacement for Yuki's Python authorization logic:

1. `POST /v1/internal/executions/authorize` — validate the signed one-shot
   grant against the exact actor/session/task/manifest and atomically consume
   the execution ID before sandbox startup. Duplicate/replayed execution IDs
   must fail closed.
2. `POST /v1/internal/executions/tool-call` — revalidate grant expiry,
   revocation, current authenticated session, account ownership, task approval,
   cancellation and tool allow-list on every call; enforce unique
   `(execution_id, call_index)`; dispatch only through the native authorized
   tool implementation; return `{ "authorized": true, "result": {...} }`.
3. `POST /v1/internal/executions/complete` — durably record terminal status,
   duration and reconciliation metadata in Yuki's audit trail. The endpoint
   must be idempotent and must not treat a Worker-provided status as proof of
   successful host-side tool execution.

The service binding must reject requests not originating from the configured
adapter identity and must never forward the grant token to logs or analytics.
The Python control plane and its existing grant authority remain authoritative;
a thin authenticated Worker-to-native bridge is required before deployment.
The bridge must also enforce per-actor/per-task concurrency and reconcile
terminal status against its own dispatch/audit records, not trust Worker claims.
Do not bind a public URL or add unrestricted egress as a substitute for this
service binding.

## Cancellation and runtime limits

Each tool RPC rechecks current Yuki task/session/grant state, so cancellation
or revocation prevents subsequent host-side tool calls. The current adapter
does not provide an out-of-band interrupt for a generated program that is
performing pure computation without calling a tool; such execution is bounded
by the configured Dynamic Worker timeout. Prompt cancellation of pure compute
requires a separately verified Worker termination/abort mechanism and must be
added before production enablement. The native control plane must also cap
concurrent executions per actor and task. Do not describe this first adapter
slice as providing immediate cancellation or production-grade concurrency
admission.

## Contract digest

`manifest_sha256` is the SHA-256 of canonical JSON for the same manifest used
by `CodeExecutionRequest.manifest_sha256`: sorted keys, compact separators,
UTF-8 encoding. The included source digest is SHA-256 over UTF-8 source bytes.
The adapter verifies both before grant authorization.

## Development and validation

From this directory:

```sh
npm install
npm run typecheck
npm test
```

Deployment is intentionally not configured in this repository yet. Configure
the Worker Loader and authenticated service binding only after the native
control-plane bridge and its integration tests exist. Keep the feature opt-in;
do not deploy with a public unauthenticated control-plane URL.


## Authenticated control-plane binding

The Worker requires a high-entropy `CONTROL_PLANE_TOKEN` secret (at least 32
bytes). Set it as a Worker secret; never commit it or place it in `vars`:

Configure `CONTROL_PLANE_TOKEN` as a Worker secret using the Cloudflare
account's approved deployment workflow or dashboard. Do not store it in
`vars`, source control, or generated deployment manifests.

Every internal service-binding RPC carries this value in the
`x-yuki-adapter-token` header. The receiving Yuki ASGI runtime must be
configured with the same value through
`build_conversation_runtime(code_execution_grant_authority=...,
code_execution_dispatcher=..., code_execution_adapter_token=...,
code_execution_audit=...)`. The ASGI bridge exposes only the three internal
routes documented above and independently resolves the session fingerprint
against `BuiltInAuthService`; the service token is not a substitute for
actor/session/task authorization.

The `CONTROL_PLANE` binding must terminate at a trusted internal Worker that
forwards these requests to a privately hosted Yuki ASGI endpoint using TLS and
the service token. Do not point generated-code workers directly at a public
unauthenticated URL. This repository does not yet include or deploy that
forwarding Worker or a hosted ASGI origin. Until both are provisioned and
end-to-end tested, keep the adapter disabled and do not expose a public route
or `workers.dev` endpoint.


## Private control-plane forwarding Worker

A separate internal-only Worker implementation lives in
`control-plane/worker.ts`. Bind the Code Mode Worker’s `CONTROL_PLANE`
service binding to this forwarder, not directly to the Yuki ASGI origin. The
forwarder:

- accepts only the three execution RPC paths and POST + JSON requests;
- verifies the adapter service token before contacting the origin;
- uses a fixed HTTPS origin (no request-controlled hostname, path, query, or
  redirects) and Cloudflare Access service credentials;
- forwards only the JSON body and the three required authentication headers;
- enforces 96 KiB request and response limits and returns only JSON responses.

Configure the following as Worker secrets/configuration, never source literals:

- `YUKI_ASGI_ORIGIN`: the HTTPS origin protected by Cloudflare Access, with no
  path, query, fragment, username, or password;
- `YUKI_ASGI_ACCESS_CLIENT_ID` and `YUKI_ASGI_ACCESS_CLIENT_SECRET`: the
  Access service-token pair;
- `CODEMODE_ADAPTER_TOKEN`: the same high-entropy adapter secret configured
  on the Code Mode Worker and Yuki ASGI bridge.

The forwarder has no public route or `workers.dev` endpoint in this repository.
Deployment configuration and service-binding creation remain an operator
step. Before deployment, configure the ASGI origin behind Cloudflare Tunnel or
another private origin path, add an Access service-auth policy for this
forwarder, and bind the Code Mode Worker to the forwarder using an internal
service binding. Do not make the ASGI API publicly reachable.
