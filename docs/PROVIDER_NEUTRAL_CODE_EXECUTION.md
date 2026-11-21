# Provider-neutral code execution contract

**Status:** contract and control-plane foundation only; no execution backend is enabled.

Yuki remains the authority for identity, task ownership, approvals, policy, tool
implementations, audit records, and result acceptance. An execution backend is
replaceable infrastructure, not an authority.

## Contract

- `CodeExecutionRequest`: immutable workload, actor/session/task binding, exact
  source digest, bounded tool manifest, timeout and output budgets.
- `ExecutionGrant`: opaque signed grant claims bound to one execution and exact
  request manifest. Its fields are not proof of authenticity.
- `ExecutionGrantVerifier`: trusted control-plane contract for signature,
  expiry, revocation, identity binding, and atomic tool-call authorization.
- `CodeExecutor`: provider-neutral backend protocol. It receives only a narrow
  host-side tool dispatcher and cancellation callback.
- `CodeExecutionResult`: bounded, explicitly untrusted result envelope.
- `CodeExecutionService`: orchestration wrapper that verifies grants, records
  mandatory audit events, checks cancellation, enforces tool and JSON RPC limits,
  and reconciles reported tool calls against actual host dispatches.
- `TaskRuntimeExecutionPolicy`: binds each grant to a task visible to the
  authenticated actor, approved by that actor for the exact session, currently
  running under the expected host-injected worker lease, and not expired. It
  rechecks durable task state at grant issuance and every tool dispatch. For
  dispatch, it holds the workflow store's SQLite write reservation across the
  bounded host operation, ordering task cancellation/lease changes against side effects.
- `build_task_bound_code_execution_runtime`: preferred application composition
  helper; wires the policy above rather than accepting a generic callback.
- `build_code_execution_runtime`: lower-level trusted composition root that derives a
  purpose-separated execution signing key from Yuki's explicitly initialized OS
  credential store, requires a live application policy callback, and adapts
  lifecycle events into the existing hash-chained `SQLiteApprovalAuditLog`; the
  composition root verifies the existing chain before enabling the service.
- `HMACExecutionGrantAuthority`: short-lived signed grant issuance, exact-request
  verification, actor-scoped revocation, and a transactional SQLite lifecycle
  journal. Its `authorize_tool_call` holds the SQLite write reservation across
  final grant verification, live Yuki policy revalidation, and the bounded host
  operation. Revocation and tool side effects are ordered rather than racing
  between check and use.

Signing key material is derived from a stable OS-keystore HMAC secret using a
purpose-specific HMAC context. It is never generated as a fallback or persisted
in the grant database. OS key-store bootstrap remains an explicit operator
action; missing key material fails closed.

Network access is rejected by this first contract. Tool names are explicit and
bounded. The request digest covers actor, session, task, execution ID, source
digest, tools, and resource limits.

## Required adapter behavior

Host tool operations dispatched through the task-bound composition must be
bounded and must not recursively acquire the same workflow-store write lock.

A concrete executor must:

1. Verify the signed grant with Yuki's trusted control plane before execution.
2. Revalidate grant validity and live Yuki policy on every tool RPC; never trust
   actor IDs or permissions asserted by generated code.
3. Route tool calls only through Yuki's host-side dispatcher, which performs the
   normal per-account authorization, approval, idempotency, and tool-specific
   checks.
4. Treat source, stdout/stderr, logs, tool results, and artifacts as untrusted.
5. Keep each host tool dispatch bounded and synchronous while the local grant
   journal serializes it against revocation; host tools still need their own
   operation-specific timeouts and cancellation semantics.
6. Enforce no network by default, strict execution/tool-call/output/time limits,
   cancellation, and confirmed cleanup.
7. Fail closed on verification, audit, timeout, cancellation, or cleanup errors.
8. Never expose host credentials, unrestricted filesystem access, shell access,
   runtime sockets, or broad account/API credentials to generated code.

Execution lifecycle events are adapted into Yuki's hash-chained SQLite approval
audit log. That log stores metadata and hashes actor/correlation identifiers; it
does not store source, generated output, tool arguments, or grant tokens. The
grant journal separately records issuance/revocation transactionally and is not
tamper-proof against a compromised host. Key rotation/recovery policy, runtime
isolation, deployment, and end-to-end security validation remain follow-up work.

## Cloudflare Code Mode

The planned optional Cloudflare adapter belongs behind `CodeExecutor`. It must
use an isolated Dynamic Worker and a host-side RPC proxy. Cloudflare credentials
and configuration remain optional; core Yuki must continue to work without them.
The adapter work is tracked in [issue #1074](https://github.com/trivenico/Yuki/issues/1074).

## Validation

- Contract tests: `tests/test_code_execution.py`.
- Orchestration tests: `tests/test_code_execution_service.py`.
- Grant issuance, verification, revocation, live-policy revalidation, and
  dispatch/revocation race tests: `tests/test_execution_grants.py`.
- OS-key-provider and central-audit composition tests:
  `tests/test_code_execution_runtime.py`.

These tests have not yet been confirmed by CI on the latest PR head. No runtime
isolation or generated-code execution is enabled by this change.
