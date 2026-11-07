# Phase 171 — Pretrained-first compute worker protocol

## Objective

Run Yuki development and lightweight inference on local machines while placing
resource-heavy inference and pretrained-model LoRA/QLoRA adaptation on approved
Linux/GPU workers. GitHub remains the source-control and CI control plane; it is
not treated as unlimited GPU compute or durable model-weight storage.

## Worker capability contract

The version-1 capability record advertises worker identity, protocol version,
paired/enabled state, CPU cores, RAM, available storage, supported backends,
GPU vendor/name/memory/backend, and heartbeat timestamp. Unknown or malformed
resource values fail closed. Stale, future-dated, unpaired, or disabled workers
are not eligible for scheduling.

The protocol adapter maps validated records into the existing
`DistributedModelScheduler` input shape. The scheduler remains a placement
planner only: it does not dispatch work or grant access.

## Trust and security boundaries

This milestone validates capability metadata only. It is not a network
authentication protocol and does not implement pairing, credential issuance,
job dispatch, sandboxing, or cloud provisioning. Those must be separate
reviewed components.

Before remote execution is enabled, require mutually authenticated transport,
short-lived worker credentials, explicit worker allowlists, signed job
specifications, lease-bound claims, least-privilege execution, secret isolation,
resource quotas, cancellation, bounded logs, and artifact digest verification.
Untrusted pull-request code must never execute on privileged GPU workers.

## Pretrained-first training

Workers execute only approved experiments referencing pinned pretrained base
model digests and verified model/dataset rights. The current roadmap uses
retrieval, memory, evaluated LoRA/QLoRA adapters and specialist capabilities;
training foundation weights from random initialization is out of scope.
