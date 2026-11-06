# Phase 137 — Isolated upgrade execution service contract

## Status

Design contract only. This phase does not implement a container runtime, VM
manager, cryptographic attestation verifier, or production execution backend.
The existing `ApprovedTestRunner` remains a host subprocess runner and must not
be treated as a sandbox. The existing
`ApprovedUpgradeExecutor.staging_workspace_validator` is an injected policy
callback, not proof of operating-system isolation.

## Objective

Move execution of untrusted proposed Yuki changes out of the long-lived Yuki
application process. The service must accept an immutable proposal snapshot,
execute only a pre-approved test plan in a disposable environment, and return
evidence cryptographically bound to the exact inputs and execution policy.

## Trust boundaries

1. **Yuki control plane** — creates proposals, persists review decisions, and
   requests an execution. It must not mount production credentials into a job.
2. **Admission service** — validates reviewer/principal identity, proposal
   approval, snapshot digest, test-plan allow-list, resource profile, and
   idempotency key. Admission is fail-closed.
3. **Execution worker** — a disposable container or VM created from a
   pinned-by-digest image. It receives only the snapshot, declared test plan,
   and minimal runtime dependencies. It has no access to the Yuki host socket,
   control-plane database, home directory, SSH agent, cloud metadata endpoint,
   or production secrets.
4. **Evidence verifier** — verifies a signed statement from an independently
   trusted runner identity. It must not trust a caller-provided string or a
   test callback that simply returns true.
5. **Promotion controller** — separately requires human approval after
   evaluation. Test success alone never deploys, commits, or promotes a model
   or source release.

## Required immutable execution request

The canonical request must bind at least:

- proposal ID and full SHA-256 snapshot digest;
- source snapshot object digest and immutable storage version;
- exact test target identifiers and test-plan digest;
- runner image digest, architecture, and runtime/toolchain versions;
- policy profile digest (mounts, network, capabilities, limits);
- unique execution ID and idempotency key;
- requesting principal, reviewer, and approval record identifiers.

Canonical serialization must be versioned and deterministic. Reject duplicate
or unknown fields where they could alter interpretation. Paths must be
normalized relative POSIX paths; reject symlinks and traversal.

## Isolation requirements

A production backend must enforce these properties outside the guest process:

- disposable container or micro-VM per execution, with no shared writable
  workspace between jobs;
- read-only runner image and narrowly scoped writable scratch volume;
- no privileged mode, host PID namespace, host networking, device access,
  container-runtime socket, or host filesystem mounts;
- default-deny egress, with no network route unless a separately reviewed
  dependency-fetch phase explicitly needs it;
- no inherited environment variables or credentials from the control plane;
- non-root identity, dropped Linux capabilities, seccomp and mandatory
  access-control profile where supported;
- CPU, memory, process-count, disk, output, and wall-clock limits;
- termination of the complete execution boundary on timeout, cancellation,
  worker loss, or policy violation;
- cleanup and verification that the disposable environment is destroyed.

A container is not automatically a strong security boundary. For hostile
model-generated code, prefer a hardened micro-VM or a managed isolated runner
with a documented threat model. Do not claim equivalent isolation across
platforms without evidence.

## Evidence statement

The runner must sign a canonical statement containing:

- execution ID, proposal ID, and snapshot digest;
- test-plan digest and individual target outcomes;
- runner image digest and policy profile digest;
- start/end timestamps, exit status, timeout/cancellation status;
- bounded stdout/stderr digests and result artifact digests;
- runner identity, issuer, audience, workflow/job identity, and nonce;
- statement schema version and signing-key identifier.

The verifier must validate the signature and claims, including issuer,
audience, authorized workflow/runner identity, nonce freshness, execution
identity, snapshot digest, image digest, policy digest, and result digest.
Persist the verified statement digest and verifier/key identity. Reject
replayed, expired, mismatched, unsigned, or unverifiable evidence.

The existing `runner_attestation_verifier` injection point is only an
extension seam. A lambda or test fixture is not a production cryptographic
verifier. Existing evidence rows and test tokens must not be represented as
proof of a live attested runner.

## State machine

`proposed -> reviewed -> admitted -> queued -> running -> evidence_pending
-> verified_pass | verified_fail | timed_out | cancelled | rejected`

Only `verified_pass` may become an input to a separate release evaluation.
Any state transition must be durable, idempotent, auditable, and bound to the
same execution ID and request digest. Unknown or contradictory terminal
results must fail closed.

## Implementation sequence and acceptance gates

1. Define versioned request, policy, result, and evidence schemas with strict
   validation and canonical digest tests.
2. Implement a real backend adapter for one explicitly supported isolated
   runtime. Do not add a fake provider or claim backend support based on a
   test double.
3. Enforce admission policy and immutable snapshot delivery; test path,
   symlink, credential, network, mount, and resource-limit denials.
4. Implement runner-side signed evidence and a real public-key/signature
   verifier with issuer/audience/subject/workflow claims and replay protection.
5. Persist execution state and verified evidence transactionally; test worker
   crashes, duplicate callbacks, cancellation, timeout, and database reopen.
6. Add an operator-reviewed promotion stage with signed artifacts, provenance,
   rollback checkpoint, and an explicit release approval.

The phase is complete only when a real isolated backend and real signature
verification run in CI or a controlled integration environment. Unit tests
with injected callbacks validate policy logic only; they do not satisfy this
gate.

## Current readiness

- Proposal snapshots and review records: implemented in the existing pipeline.
- Digest-bound workspace handoff and second approval: implemented, but host
  workspace editing is not isolation.
- Host-based allow-listed test runner: implemented, explicitly not a sandbox.
- Immutable isolated execution service: not implemented.
- Production cryptographic runner attestation: not implemented.
- Signed release artifact and rollback controller: not implemented.

Desktop app, CLI, and companion assistant remain first-class Yuki deliverables.
The isolated execution service is a control-plane capability and must expose
operator-visible status and evidence through those interfaces once implemented.
