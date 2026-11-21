# Provider-neutral code execution contract

**Status:** contract foundation only; no execution backend is enabled.

Yuki remains the authority for identity, task ownership, approval, policy, tool
implementations, audit records, and result acceptance. An execution backend is
replaceable infrastructure, not an authority.

## Contract

`core/code_execution.py` defines:

- `CodeExecutionRequest`: immutable workload, actor/session/task binding, exact
  source digest, bounded tool manifest, timeout and output budgets.
- `ExecutionGrant`: opaque signed grant claims bound to one execution and exact
  request manifest. Its fields are not proof of authenticity.
- `ExecutionGrantVerifier`: trusted control-plane verification contract for
  signature, expiry, revocation, and identity binding.
- `CodeExecutor`: provider-neutral backend protocol. It receives only a narrow
  host-side tool dispatcher and cancellation callback.
- `CodeExecutionResult`: bounded, explicitly untrusted result envelope.\n- `CodeExecutionService`: control-plane wrapper that verifies the grant, records mandatory audit events, rechecks grant validity/revocation at every host tool RPC, enforces per-RPC JSON bounds and cancellation, and reconciles executor-reported tool counts against actual host dispatches.

Network access is rejected by this first contract. Tool names are explicit and
bounded. The request digest covers actor, session, task, execution ID, source
digest, tools, and resource limits.

## Required adapter behavior

A concrete executor must:

1. Verify the signed grant with Yuki's trusted control plane before execution.
2. Revalidate grant validity and tool authorization on every tool RPC; never trust
   actor IDs or permissions asserted by generated code.
3. Route tool calls only through Yuki's host-side dispatcher, which performs the
   normal per-account authorization, approval, idempotency, and audit checks.
4. Treat source, stdout/stderr, logs, tool results, and artifacts as untrusted.
5. Enforce no network by default, strict execution/tool-call/output/time limits,
   cancellation, and confirmed cleanup.
6. Fail closed on verification, audit, timeout, cancellation, or cleanup errors.
7. Never expose host credentials, unrestricted filesystem access, shell access,
   runtime sockets, or broad account/API credentials to generated code.

The interface does not itself authenticate, sign, verify, or execute anything.
It must not be described as a sandbox or production execution capability.

## Cloudflare Code Mode

The planned optional Cloudflare adapter belongs behind `CodeExecutor`. It must
use an isolated Dynamic Worker and a host-side RPC proxy. Cloudflare credentials
and configuration remain optional; core Yuki must continue to work without them.
The adapter work is tracked in [issue #1074](https://github.com/trivenico/Yuki/issues/1074).

## Validation

The focused contract tests live in `tests/test_code_execution.py`. Runtime
isolation, signed grant issuance/verification, audit orchestration, tool RPC,
deployment, and end-to-end security validation remain separate follow-up work.
