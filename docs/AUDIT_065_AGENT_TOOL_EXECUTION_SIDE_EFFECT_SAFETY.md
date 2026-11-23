# Yuki Engineering Audit 065 — Agent Execution Pipeline, Tool Dispatch, and Side-Effect Safety

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source and test inspection  
**Status:** Documentation audit plus a separate implementation PR for one confirmed request-boundary defect. Tests have not been executed.

## Executive assessment

Yuki has multiple deliberate safety layers: provider-neutral structured tool proposals, bounded JSON envelopes, a guarded runtime, policy checks, one-shot skill approvals, durable actor/session-bound approvals, metadata-only audit records, and a separate owner/account-bound MCP policy system. The agent loop also bounds model turns, tool calls, transcript size, and checks cancellation before dispatch.

The audit found a confirmed gap in the central agent loop: the model's proposed tool names were not checked against the tools advertised for that specific request. The global guarded runtime could still authorize any registered tool by name. A tool could therefore be globally registered and policy-authorized but not exposed to the model for the current request. This has been addressed in a separate code PR by enforcing a request-scoped allow-list before dispatch.

Other important limitations remain:
- Generic guarded tool authorization receives only a tool name, not a trusted principal, tenant, resource, action digest, or delegated grant.
- The structured protocol validates envelope shape and JSON argument size, but does not validate arguments against the tool's JSON Schema.
- Structured call IDs are unique only within one parsed batch; they are not durable idempotency keys.
- Generic tool handlers execute synchronously without a runtime-enforced deadline or cancellation contract.
- Approval and MCP paths are stronger than the generic guarded CLI tool path; do not assume every tool invocation inherits those guarantees.

## 1. Agent execution loop

### `core/agent_execution_loop.py`

Observed:
- Synchronous multi-turn model/tool/model loop.
- Bounds model turns, total tool calls, duration budget, transcript size, tool definitions, and history.
- Validates model response envelope and parses the complete structured tool batch before dispatch.
- Checks cancellation between model turns and immediately before dispatch.
- Emits lifecycle events; event journal failures propagate rather than being silently ignored.
- Tool results are returned as explicitly untrusted evidence.

Confirmed issue and implementation:
- Before the fix, the loop accepted any syntactically valid tool name in the model response and delegated authorization to the global guarded runtime. It did not verify that the name was in the current request's advertised `tools` list.
- This was a request-level capability boundary omission, not a bypass of the global tool policy itself.
- A separate implementation PR now validates the per-request function definitions, rejects duplicate/malformed names, and rejects model proposals outside the advertised set before dispatch.

Remaining recommendations:
1. Bind every execution to trusted user/tenant/session and agent identity supplied by the host, never model text.
2. Pass a typed execution context through the loop, policy, tool runtime, and audit sink.
3. Enforce deadlines/cancellation within the actual tool execution boundary; a check before dispatch cannot stop a handler already running.
4. Record a stable request/task ID and policy revision in every execution event.
5. Ensure streaming cancellation closes the model stream and cannot leave a later tool dispatch possible.

## 2. Structured tool protocol

### `core/structured_tool_protocol.py`

Observed:
- JSON response size is capped; at most five calls per batch.
- Each call must contain exactly ID, name, and object arguments.
- IDs are bounded and unique within the batch.
- Arguments must serialize as JSON and fit a byte limit.
- The entire batch is parsed before any call is dispatched, preventing malformed later entries from causing partial execution.
- Dispatch delegates to `GuardedToolRuntime`.
- Results are marked `untrusted_tool_output`.

Limitations:
- Call IDs are not persisted or checked across batches, sessions, or process restarts. Replaying the same envelope can execute the same action again.
- The protocol does not validate argument objects against the advertised function parameter schema. JSON-object shape and byte size do not prove semantic validity.
- The protocol itself has no execution deadline, cancellation token, or per-call timeout.
- A batch is syntactically validated before dispatch, but tool execution is sequential and earlier side effects are not rolled back if a later call fails.

Recommendations:
1. Validate each call against a trusted, versioned tool schema at the execution boundary; reject unknown fields and invalid types/ranges.
2. Treat provider-supplied schemas as presentation/contract data, not authorization policy.
3. Introduce durable idempotency keyed by tenant, task/request, call ID, tool name, and canonical argument digest.
4. Detect same key with different arguments as a conflict; do not silently reuse results.
5. Define batch semantics explicitly: independent calls, dependency order, stop-on-failure, and compensation/reconciliation.
6. Require explicit side-effect classification and per-tool retry policy.

## 3. Generic guarded tool runtime

### `core/guarded_tool_runtime.py`

Observed:
- Registry is an explicit dictionary of validated names and callables.
- Policy must implement `authorize_tool(name)`; policy exceptions fail closed.
- Argument and result character limits are enforced.
- Handler exceptions are hidden from the caller.
- Audit records contain tool name and outcome only; audit callback failures are swallowed.
- Handler is called synchronously in the caller's thread.

Important gaps:
- Authorization is name-only. It cannot distinguish principals, tenants, resources, argument-sensitive actions, or delegated agent scopes unless the injected policy maintains hidden ambient context.
- There is no per-handler timeout, cancellation, concurrency cap, or process isolation.
- Audit is best-effort in this generic runtime. That may be acceptable for optional telemetry, but it is not equivalent to the durable fail-closed approval audit path.
- Truncating a result does not bound the work or memory consumed while the handler constructs it.

Recommendations:
1. Replace the name-only callback with a typed authorization request: principal, tenant, agent, tool, action, resource, argument digest, risk, policy version, and delegation grant.
2. Re-authorize immediately before side effects and bind the decision to immutable arguments.
3. Distinguish optional telemetry from mandatory security audit; high-risk execution must not start unless its required pre-execution audit/approval record is durable.
4. Enforce wall-clock deadline, cancellation, concurrency, output bytes, and process/resource budgets at the handler boundary.
5. Isolate untrusted or non-cooperative handlers in a sandbox/process boundary.
6. Make handler registration immutable/versioned during an execution or bind each request to a stable handler object.

## 4. Approval flows

### Inspected
- `core/approval_flow.py`
- `core/pending_approvals.py`
- `core/approval_audit.py`
- `core/action_approvals.py`
- `core/permissions.py`
- `core/tiered_autonomy.py`
- `core/guarded_skill_runtime.py`

Strengths:
- `ApprovalCoordinator` snapshots the exact action, binds pending requests to actor and session, consumes approvals one time, and prevents replay.
- SQLite pending approvals have TTL, bounded capacity, atomic claim semantics, and hashed actor/session bindings.
- Approval audit is append-only and hash chained; failure to record the approval event prevents execution.
- Confirmation callback receives a defensive copy, so mutating the display object does not mutate the stored action.
- Generic skill invocation approvals are one-shot and bound to exact skill name and argument digest.
- Permission and autonomy gates fail closed when approval is unavailable; autonomy tiers do not bypass per-action approval.

Remaining checks:
- Ensure the identity passed to the coordinator is derived from authenticated server-side context, not caller-controlled headers or model arguments.
- Bind approval to tenant, exact target resource, action digest, policy revision, and expiry at the service boundary.
- Review the legacy in-memory `ActionApprovalStore` separately; it is not equivalent to the durable TTL-backed store.
- Ensure all high-risk execution paths use the durable approval coordinator and cannot call a lower-level handler directly.
- Define recovery behavior for a side effect that succeeds but the post-execution audit write fails; the existing `recovery_required` result is important and must not be retried as a normal failure.

## 5. MCP tool execution

### Inspected
- `core/mcp_tool_policy.py`
- `core/mcp_runtime.py`
- `core/mcp_tool_policy_store.py`
- `core/mcp_client.py`
- `core/mcp_execution_audit.py` (audit integration through shared approval audit module)

Observed strengths:
- MCP policy is host-reviewed; remote tool metadata is not an authorization source.
- Policy binds owner, provider, account, tool name, risk, scopes, and version.
- Write and financial tools require fresh literal-True owner confirmation; audit failure denies access.
- Runtime is composed from trusted host configuration and authenticated owner identity.
- Policy revisions can fence stale runtime state.
- Endpoint configuration is constrained to trusted HTTPS hosts.

Recommendations:
- Keep MCP as a separate stronger policy boundary; do not generalize its guarantees to every local tool.
- Test revocation races during in-flight calls, scope removal, provider account switching, and policy revision changes.
- Verify that the exact canonical argument digest shown for approval is the digest used for execution.
- Add durable idempotency for remote side effects and explicit unknown-outcome reconciliation.
- Ensure provider response text is always untrusted and cannot alter policy or approval state.

## 6. Sandbox and execution isolation

### Inspected
- `core/sandbox_policy.py`
- `core/sandbox_runtime.py`
- `core/guarded_skill_runtime.py`

Observed:
- Sandbox policy requires immutable capability sets, non-root execution, read-only root, bounded time/memory/CPU/process/output values, and explicit network policy.
- Docker command builder requires digest-pinned images and conservative runtime flags.
- Capability declarations are explicitly not treated as attestation; without a trusted host-side verifier, validation fails closed.
- Guarded skill runtime itself documents that it does not sandbox skill code.

Recommendations:
1. Do not infer that every registered tool or skill is sandboxed because sandbox primitives exist in the repository.
2. Map each executable tool to a verified isolation backend or an explicit trusted-code classification.
3. Test actual host enforcement: namespaces, seccomp, MAC, cgroups, mounts, network egress, secrets exclusion, cleanup.
4. Make sandbox unavailability a hard denial for tools whose risk profile requires isolation.

## 7. Test coverage and required additions

Existing focused tests include:
- `tests/test_agent_execution_loop.py`: bounded turns, cancellation checks, tool budget, transcript and history validation.
- `tests/test_guarded_tool_runtime.py`: allow/deny behavior, argument/result bounds, exception redaction, policy failure.
- `tests/test_structured_tool_protocol.py`: batch parsing, duplicate IDs, malformed calls, denial, untrusted outputs.
- `tests/test_approval_flow.py` and `tests/test_approval_coordinator_security.py`: durable approval, actor/session binding, mutation resistance, replay, concurrency, audit failures.
- `tests/test_mcp_tool_policy.py`, `tests/test_mcp_runtime.py`, and MCP execution tests: policy scopes, approvals, audit, and runtime composition.
- `tests/test_sandbox_policy.py` and `tests/test_sandbox_runtime.py`: fail-closed policy and command construction.

Add:
1. Model proposes a registered but unadvertised tool; verify no handler runs (regression test added in implementation PR).
2. Schema-invalid arguments rejected before any side effect.
3. Same call ID replayed in a later batch/process; verify durable idempotency.
4. Same idempotency key with different argument digest; verify conflict.
5. Cancellation during a running tool; verify process/handler termination or explicit non-cancellable contract.
6. Deadline expiry while handler is blocked.
7. Policy revocation between model response and actual side effect.
8. Cross-tenant resource substitution and argument tampering.
9. Partial batch failure with earlier side effect completed; verify reconciliation behavior.
10. Mandatory audit sink outage before a high-risk action; verify execution is denied.
11. Direct invocation paths cannot bypass the guarded runtime.
12. Tool registry replacement during approval/execution; verify stable handler binding.

## 8. Prioritized remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Enforce request-scoped advertised tool allow-list | Regression test; implementation PR opened |
| P0 | Bind generic tool authorization to trusted principal/tenant/resource/action | Cross-tenant and privilege-escalation tests |
| P0 | Enforce tool deadlines and cancellation at execution boundary | Hung handler and cancellation integration tests |
| P1 | Validate arguments against trusted tool schemas | Negative schema and no-side-effect tests |
| P1 | Durable call idempotency and replay protection | Multi-process replay tests |
| P1 | Require durable pre-execution audit for high-risk tools | Audit outage fault-injection tests |
| P1 | Ensure all execution paths use risk-appropriate isolation | Tool-to-sandbox inventory and host enforcement tests |
| P2 | Version and freeze tool registry/handler binding | Concurrent registry mutation tests |
| P2 | Formalize batch compensation/reconciliation | Partial side-effect failure tests |

## Verification status

This audit is based on targeted source and test inspection. The request-scoped tool exposure defect has a separate implementation PR with a regression test added, but the test has not been run. No test suite, sandbox host enforcement test, penetration test, cross-tenant test, side-effect replay test, or production tool execution was performed.
