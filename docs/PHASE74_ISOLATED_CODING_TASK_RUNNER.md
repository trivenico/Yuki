# Phase 74 — Isolated coding-task runner

This phase adds a bounded Docker execution adapter for coding tasks. It is deliberately separate from task planning and patch review.

## Controls

- Requires a trusted approval callback on every invocation; callback exceptions and anything other than literal True deny execution.
- Uses the existing fail-closed sandbox policy and Docker argv builder. Commands are argument arrays; host shell execution is disabled.
- No network, read-only root filesystem, non-root container UID, dropped Linux capabilities, explicit read-only workspace by default, and CPU/memory/process limits come from the existing sandbox builder.
- Gives each container a random name, disables Docker auto-removal for this runner, and requires explicit cleanup confirmation.
- Enforces the policy timeout, bounds argv count/size, and applies a byte cap while draining combined stdout/stderr. The Docker CLI receives only a small environment allowlist.
- Does not install dependencies, apply generated patches, commit, push, create pull requests, or merge changes.

## Important limitations

- Requires a functioning, appropriately configured Docker daemon and a trusted, pinned image. Image trust and digest pinning remain deployment configuration responsibilities; the caller must use a trusted immutable image reference.
- Approval identity/session binding must be implemented by the caller using Yuki's trusted approval coordinator; this runner intentionally accepts a callback rather than inventing an identity source.
- Cleanup failure is reported as an execution failure. Production deployments still need an external supervisor/lease cleanup and monitoring for daemon or host failure.
- The current sandbox builder refuses allowlist networking unless a separately enforced egress gateway exists.
- The containerized test runner is not yet connected to the durable coding-task queue or patch-evidence workflow. That integration requires a further phase and end-to-end tests.
