# Tool Authorization Boundary Review

**Status:** design review recorded; no new host capabilities enabled  
**Scope:** `core/agent_execution_loop.py`, `core/structured_tool_protocol.py`,
`core/guarded_tool_runtime.py`, `core/approval_flow.py`,
`core/pending_approvals.py`, and `core/guarded_skill_runtime.py`.

## Current security posture

The default local CLI constructs an empty guarded-tool registry and a deny-all
policy. Persistent chat therefore cannot execute shell commands, modify files,
or invoke host tools. The structured tool protocol is not itself an authorization
mechanism; it validates model-proposed JSON and delegates dispatch to the guarded
runtime.

The existing skill path has stronger approval primitives than the generic tool
runtime:

- `OneShotSkillApprovals` binds a one-time approval to the skill name and the
  SHA-256 digest of the exact UTF-8 argument string. Consumption is atomic within
  the process.
- `ApprovalCoordinator` snapshots the complete action, binds a pending request
  to actor and session, displays a defensive copy for confirmation, and consumes
  the request once.
- `SQLitePendingApprovalStore` persists bounded pending actions, enforces a
  short TTL, checks actor/session bindings, and claims a request atomically.
- Approval audit failure before execution prevents execution. An audit failure
  after execution is reported as recovery-required rather than as a retryable
  failure.

These controls are separate layers. A persistent skill-registration approval
does not replace per-invocation approval, and neither approval mechanism
sandboxes the skill implementation.

## Finding: generic tool policy is name-only

`GuardedToolRuntime.invoke(name, argument)` currently calls
`policy.authorize_tool(name)`. The policy does not receive the argument,
canonical action digest, owner/session identity, requested scope, or approval
reference. A policy that permits a tool name therefore cannot distinguish
between materially different operations made through that tool.

This is not a currently reachable CLI bypass: the CLI has no registered tools
and uses a deny-all policy. It is a blocking design gap before any mutating or
externally acting tools are registered.

## Required contract before enabling tools

1. Parse and validate the entire proposal before any dispatch.
2. Canonicalize the action using a documented, deterministic JSON encoding;
   bind approval to tool identity, exact canonical arguments, and the relevant
   target/resource scope. Reject duplicate keys, non-finite numbers, invalid
   Unicode, unknown fields, and oversized payloads.
3. Bind the approval to a trusted owner principal and authenticated session.
   Never derive owner identity from model output or tool arguments.
4. Require a fresh, one-time approval for every state-changing or external
   action. Approval must expire, be atomically consumed, and be unusable for a
   different argument set, target, tool implementation, or session.
5. Keep read-only capabilities separately allow-listed and narrowly scoped;
   do not infer read-only behavior from a model-provided description.
6. Fail closed if authorization, approval persistence, audit-before-execution,
   identity resolution, or scope validation is unavailable.
7. Record metadata-only audit events with correlation IDs and outcome states.
   Do not store secrets, raw prompts, or sensitive arguments in the audit log.
8. Treat tool output as untrusted evidence. It must never grant permissions or
   alter the active policy.
9. Execute risky tools only through a separately hardened sandbox/runtime.
   Approval is not isolation, and a same-user process is not a security
   boundary against other code running under that user.
10. Keep the default CLI registry empty until the complete authorization,
    sandbox, recovery, and regression-test path is wired end to end.

## Follow-up implementation sequence

- Define an immutable action envelope and canonical digest format.
- Introduce an authorization interface that receives the complete immutable
  action context rather than only a tool name.
- Add owner/session-bound, expiring, one-time approval tokens for that envelope.
- Test argument substitution, target substitution, tool replacement, replay,
  expiry, concurrent consumption, identity mismatch, and audit-store failure.
- Wire only explicitly reviewed low-risk read-only tools first. Keep all
  mutating tools disabled until their sandbox and recovery controls are proven.

## Non-goals

This review does not enable shell execution, browser control, arbitrary file
writes, dynamic skill imports, self-modification, or autonomous permission
changes. It does not claim the native Linux runner is a production sandbox.
