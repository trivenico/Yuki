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
