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


### Guarded-dispatch consumption adapter

Added `OneShotToolApprovalPolicy` as an adapter between the grant store and
`GuardedToolRuntime`. Composition supplies one approval ID and one trusted
target-scope string. The policy atomically consumes the grant for the exact
envelope, then appends a durable `approved` audit event before returning
authorization. The guarded runtime invokes the handler only after that returns
literal `True`. The policy instance is itself single-use. Audit failure denies
dispatch and deliberately does not restore the grant.

Tests cover successful exact dispatch, argument mismatch, owner/session
mismatch, one-use policy behavior, and audit-storage failure.

This still does not create or authenticate the owner confirmation UI, nor
derive target scope from a trusted filesystem/resource resolver. The
`approval_id` and `target_scope` must be supplied by trusted application
composition and must never come from model arguments. Current CLI composition
continues to use an empty tool registry and deny-all policy. No host tools are
enabled by this change.

The audit event records approval consumption before handler invocation; it is
not an exactly-once side-effect journal. If the process crashes after consuming
the grant but before/during the handler, the action outcome is uncertain and
must be reconciled by an operator rather than automatically retried.


### Authenticated approval gateway (2026-11-27)

Added `AuthenticatedApprovalGateway` to bind approval requests and
confirmations to a live `BuiltInAuthService` session obtained from a trusted
session-token provider. Request creation requires `workflow:execute`;
confirmation re-resolves the session and requires `workflow:approve`. The
pending store binds the request to the authenticated user and the exact session
token (hashed at rest), so another session for the same account cannot approve
the pending request. The bearer token is never returned in outcomes or passed
to the confirmation callback. The gateway returns generic errors rather than
propagating action-bearing exception messages.

Regression tests cover authenticated request/confirmation, absent and revoked
sessions, least-privileged users, session substitution, and explicit denial.

### Critical integration boundary

This gateway is an authentication adapter around the existing
`ApprovalCoordinator`; it does not itself implement an owner UI, trusted
resource resolver, or guarded-tool executor. The coordinator's existing
`execute_approved_action` delegation remains a separate legacy task/skill
execution path. Do not use this gateway to enable arbitrary host tools or claim
that the exact-action `GuardedToolRuntime` is now the coordinator's executor.
Before production use, trusted composition must route approved operations into
a reviewed executor that independently resolves resource scope and enforces
the exact-action grant at dispatch. CLI remains deny-all and no tool registry
has been enabled.


### Canonical workspace resource scopes (2026-11-27)

Added `WorkspaceResourceScopeResolver` and immutable
`WorkspaceResource` values. The resolver accepts bounded relative paths only,
rejects absolute/drive paths, traversal components and control characters,
normalizes Windows separators, resolves existing symlinks, and rejects paths
whose canonical target escapes the configured workspace. It can resolve a
not-yet-created leaf when its existing ancestor remains inside the workspace.
The resulting versioned scope ID binds the workspace identity, canonical root,
and canonical relative resource path.

Regression tests cover existing and new leaves, traversal (including
Windows-style separators), absolute/drive/UNC paths, symlink escape, symlinks
that remain inside the workspace, explicit root access, stable scope identity,
and invalid roots.

### Scope resolver is not a file-operation sandbox

The resolver does not open or mutate resources and does not close filesystem
time-of-check/time-of-use races. A path can be replaced after resolution.
Production file handlers must use descriptor-relative operations rooted at an
already-open workspace directory, reject symlink traversal at each component
(for example with `openat`/ `O_NOFOLLOW` or an equivalent platform-specific
primitive), validate file type and permissions, and keep the directory handle
anchored for the complete operation. Never authorize a path and later reopen
an unchecked absolute path.

The resolver is not yet wired into approval issuance or guarded dispatch. The
trusted composition root must derive the scope ID from the resolver and bind it
to the exact action grant; model-provided `target_scope` remains untrusted.
No file tools have been registered or enabled in the CLI. These tests are
authored but have not been executed in this environment.


### Descriptor-relative workspace file access (2026-11-27)

Added `SecureWorkspaceFiles`, a POSIX-only file access layer requiring
`O_NOFOLLOW` and `O_DIRECTORY`. It anchors an open directory descriptor to
the configured workspace, revalidates the immutable resource scope before
each operation, walks parent directories using descriptor-relative
`openat`-style calls with no-follow semantics, and opens final read targets
without following symlinks. Reads require regular files and enforce a byte cap.

Writes enforce a byte cap and owner-only mode, create an unpredictable
exclusive temporary file in the already-open parent directory, write and
fsync it, then rename it relative to the same parent descriptor and fsync the
directory. This prevents path-name substitution from redirecting operations
outside the anchored workspace. A write failure after rename/fsync can still
leave an outcome that requires inspection; callers must not blindly retry
non-idempotent higher-level actions.

Tests cover regular-file reads, atomic replacement, creation with private
permissions, byte limits, closed handles, cross-workspace resource rejection,
and deterministic symlink replacement races at both the parent and final
components. The race tests verify that external target contents remain
unchanged.

### Remaining restrictions

This is a low-level file primitive, not a complete file-tool product surface.
It does not create directories, support Windows, enforce user-facing owner
confirmation, or integrate with the guarded exact-action dispatcher. It also
does not provide a global lock against a separate process with write access to
the workspace; safety comes from pinned directory descriptors and no-follow
component opens. The workspace root and its parent must be provisioned and
permissioned by trusted installation code. The CLI remains deny-all and no
host file tool is registered.

The path resolver rejects empty, dot, and parent components before canonical
resolution. Both resolver and file-access tests are authored but have not
been executed in this environment. CI results must be checked before treating
this implementation as validated.
