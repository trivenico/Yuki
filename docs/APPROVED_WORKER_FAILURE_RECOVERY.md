# Approved Worker Failure Recovery

## Security invariant

An approval-bound task is assigned to the worker that received its credential.
A task lease expiring does **not** prove that the worker has lost the credential,
stopped executing, or lost network access. Therefore lease recovery must never
clear `task_worker_assignments` or make the task claimable by another worker
by itself.

The current implementation intentionally fails closed: an assignment remains
in place after a worker disappears. This can reduce availability, but avoids
silently transferring an outstanding capability to another worker.

## Current behavior

- `DurableTaskEngine.claim()` excludes tasks assigned to a different worker.
- Expired task leases can return a task to the queued state, but do not remove
  its worker assignment.
- The credential broker binds delivery and resolution to its configured worker
  identity and consumes an available credential record once.
- The broker's injected `seal`/`unseal` adapter is not itself a production
  KMS implementation. Production deployment must use a reviewed authenticated
  encryption adapter and protect its keys independently.
- There is no automatic reassignment operation. Operators must not edit the
  SQLite assignment table manually to unblock a task.

## Required recovery protocol (not yet automated)

A future authenticated recovery service must perform all of the following
before changing a task's worker binding:

1. Authenticate an operator with a dedicated recovery permission and record
   the operator, reason, task, old worker, new worker, and approval request.
2. Confirm the task is not running under a live lease. Expire/revoke the old
   worker's credential through the authoritative approval gateway and broker.
3. Prove the old credential can no longer authorize execution. A task lease
   timeout alone is insufficient. If the old worker may have copied or already
   consumed the credential, wait for its authoritative expiry and require
   gateway-side revocation/expiry confirmation.
4. Fence the old worker identity or its execution generation so late heartbeats,
   completion attempts, and delayed messages cannot regain authority.
5. Reconstruct the policy request from trusted durable task state and obtain a
   fresh approval for the new worker. Do not transfer the old token.
6. Atomically replace the assignment only while the task is queued, the prior
   assignment and approval request still match, the old credential is
   irreversibly invalid, and the new approval hold is live.
7. Append an immutable audit event and test races between claim, lease recovery,
   credential resolution, revocation, and reassignment.

If any prerequisite cannot be established, recovery must stop and leave the
task unavailable for another worker. Human review and a new task/approval may
be safer than reassignment.

## Production gate

Do not enable automated worker reassignment until the gateway, broker, worker
identity/fencing model, and task engine share a tested recovery protocol. The
current SQLite components are local single-host stores; this document does not
claim a cross-database distributed transaction or multi-host atomicity.
