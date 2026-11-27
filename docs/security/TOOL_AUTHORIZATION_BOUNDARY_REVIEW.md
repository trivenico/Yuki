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


## Implemented first-stage contract (2026-11-27)

The generic guarded runtime now requires `authorize_action(action)`; the legacy
name-only `authorize_tool(name)` interface is rejected at construction. Before
policy evaluation, it creates a frozen `ToolActionEnvelope` containing:

- tool name;
- SHA-256 digest and UTF-8 byte length of the exact argument string passed
  to the handler;
- a process-local Python callable code fingerprint;
- trusted owner/session context supplied by the runtime composition.

The registered callable fingerprint is checked again immediately before
authorization and dispatch. The policy must return the literal boolean
`True`; exceptions and non-boolean truthy values deny execution. The envelope
does not expose raw arguments to the policy or audit callback. Tests cover
argument substitution, identity propagation, legacy-policy rejection, and
fail-closed policy errors.

The local CLI deny-all policy has been migrated to the new interface and
remains deny-all with an empty registry.

### Explicit limitations of this first stage

- The callable fingerprint is a process-local code identity, not a signed
  artifact identity or a durable release digest. Python `marshal` data can vary
  by Python version and does not bind closure cells, mutable callable-instance
  state, imported dependency state, or the runtime environment. It is not a
  substitute for a pinned tool package/build manifest.
- The envelope carries owner/session values only when the trusted composition
  supplies them. A permissive policy must reject missing identity; the envelope
  itself does not authenticate an owner or session.
- This change does not issue or consume approval tokens, validate target scopes,
  or make a read-only/mutating classification. No policy is enabled by this
  change, and no tool has been added to the CLI registry.
- The handler executes in-process after authorization. This is not a sandbox.

Next: define a versioned canonical action representation with explicit target
scope and tool-manifest identity, then bind the durable one-shot approval store
to its digest plus authenticated owner/session and revalidate policy immediately
before dispatch. Keep mutating tools disabled until execution isolation and
uncertain-outcome recovery are independently addressed.


## Durable exact-action approval grants (2026-11-27)

Added `core/tool_action_approvals.py` with a separate SQLite grant store.
Each grant binds a versioned canonical digest over the tool name, exact argument
digest and byte length, process-local implementation fingerprint, and explicit
target-scope string. The grant also binds hashed owner and session identities,
has a bounded TTL/capacity, and is atomically consumed once under
`BEGIN IMMEDIATE`. A mismatched action/scope/identity does not consume a valid
grant; expired grants are rejected and deleted. The store can participate in a
caller-supplied `MaintenanceFence`.

Regression tests cover argument, scope, implementation, owner and session
substitution; expiry; capacity; missing identity; and concurrent one-time
consumption. Tests are authored only and have not been run locally.

### Integration and trust limitations

This is a persistence primitive, not an approval UI or complete authorization
policy. No current CLI path issues or consumes these grants, and the guarded
runtime has not been changed to treat a grant as permission. A trusted owner
confirmation flow must issue a grant only after presenting the exact action;
the dispatcher must consume it immediately before execution and revalidate
session/policy. The target-scope string must come from a trusted resolver, not
from model-provided text.

The SQLite store accepts an optional maintenance fence; production composition
must inject the canonical private-profile fence and include this database in
backup/restore and recovery inventories before relying on it. It does not
authenticate owner/session identifiers, bind a signed tool release, sandbox
execution, or provide exactly-once semantics for external side effects. An
execution crash after grant consumption requires explicit uncertain-outcome
reconciliation; never retry automatically.
