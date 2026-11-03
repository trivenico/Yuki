# Isolated skill execution contract

**Status:** Proposed architecture; documentation only. **Security posture:** fail closed; this document does not enable execution.

## 1. Existing systems and decision

Keep three concerns separate:

- `core.coding_task_runner` is an approval-gated Docker runner for coding-task commands.
- `core.skill_environment_manager` produces a deterministic, hash-locked offline virtual-environment plan. It does not provision or execute it.
- `core.durable_skill_worker` currently invokes `ApprovedSkillRuntime` in-process. It is not an OS sandbox.

Do not pass registered Python skill objects into a container or serialize arbitrary Python objects. Add a data-only invocation protocol and dedicated adapter, reusing hardened Docker primitives only after the runner API is extended and independently reviewed. A venv is dependency isolation, not a security boundary.

## 2. Versioned data-only manifest

Canonicalize UTF-8 JSON (sorted keys, compact separators, no NaN/Infinity, duplicate keys rejected) before hashing. Protocol v1 requires:

| Field | Rule |
|---|---|
| protocol_version | Exact supported integer |
| skill_id / skill_version | Bounded identifiers; no path separators |
| artifact_sha256 | SHA-256 of exact reviewed bundle bytes |
| entrypoint | Relative module/function identifier; no traversal |
| input_schema_sha256 / output_schema_sha256 | Digests of reviewed JSON schemas |
| runtime_image | Immutable OCI reference ending `@sha256:<64 lowercase hex>` |
| environment_lock_sha256 | Phase 101 lockfile/plan digest |
| capabilities | Sorted, unique, explicitly supported set |
| resource_limits | Bounded time, memory, CPU, PIDs, input and output bytes |
| network | Initially exactly `none`; allowlist requires a separate egress gateway |
| mount_policy | Per-invocation staging only; never full workspace by default |

Reject unknown fields, duplicate keys, unsupported versions, mutable image tags, unknown capabilities, missing hashes, and non-canonical values. Never trust a digest supplied by the skill; compute it from staged bytes.

Illustrative manifest (placeholders are not executable authorization):

```json
{
  "protocol_version": 1,
  "skill_id": "report_builder",
  "skill_version": "1.0.0",
  "artifact_sha256": "<64 lowercase hex>",
  "entrypoint": "package.entry:run",
  "input_schema_sha256": "<64 lowercase hex>",
  "output_schema_sha256": "<64 lowercase hex>",
  "runtime_image": "registry.example/yuki-runtime@sha256:<64 lowercase hex>",
  "environment_lock_sha256": "<64 lowercase hex>",
  "capabilities": ["read_staged_input"],
  "resource_limits": {
    "timeout_seconds": 30,
    "memory_mb": 256,
    "cpu_millis": 500,
    "max_processes": 16,
    "max_input_bytes": 65536,
    "max_output_bytes": 262144
  },
  "network": "none",
  "mount_policy": "invocation_staging_only"
}
```

## 3. One-shot approval binding

Approval is one-shot and binds: authenticated actor ID; tenant/workspace ID; task ID and unique invocation ID; skill ID/version; manifest, artifact and image digests; entrypoint and input-schema digest; canonical argument digest (not raw arguments); exact capability set and resource limits; staging digest and read/write grants; expiry, approval ID and approval-store revision.

Any mismatch, expiry, replay, missing actor, or changed approval revision denies launch before Docker is called. The model, skill, task payload and UI cannot supply a trusted `approved=true` flag. Resolve authorization through the trusted coordinator on every invocation.

## 4. Worker protocol

Use bounded UTF-8 JSON request/response containing protocol version, invocation ID, manifest/artifact digests, canonical arguments and staged-input references. Validate schema and digests before importing the approved entrypoint.

- Fixed runtime entrypoint and trusted argv; never shell command strings.
- Explicit bounded stdin/stdout support (the current coding runner uses `stdin=DEVNULL`).
- stdout is protocol-only; stderr is separately bounded/redacted; validate response schema and byte limits.
- No ambient application secrets, Docker socket, host IPC/PID namespace, privileged mode, or host filesystem mounts.
- Read-only root, non-root UID, dropped capabilities, no-new-privileges, private IPC, no network by default, CPU/memory/PID/time/output limits.
- Digest-pinned image from a reviewed provenance pipeline.

Valid JSON from a container is still untrusted input.

## 5. Staging and filesystem rules

Create a fresh restrictive-permission staging directory per invocation, mounted at a fixed container path. Default read-only. Bind any write grant to a specific output subdirectory and the approval record.

Reject absolute paths, traversal, NULs, alternate separators where relevant, symlinks, hard-link escapes, and paths resolving outside staging. Never mount the full workspace, home directory, source checkout, credential directories, application database, or Docker socket. Collect only declared outputs; bound file count, size and type; verify hashes. Cleanup failure is a security event and must never appear successful.

The current Docker runner accepts a caller-supplied workspace bind mount. The skill adapter must not pass the normal Yuki workspace to it.

## 6. Durable lifecycle and recovery

```text
queued -> claimed -> authorized -> launch_intent -> running
       -> succeeded | failed | timed_out | cancelled
       -> uncertain_reconciliation_required
```

1. Persist actor-attributed authorization and durable `launch_intent` before launch. If this write fails, do not launch.
2. Persist invocation/container identity before execution can become externally observable.
3. Persist terminal status, bounded metadata, output hashes, cleanup result and audit linkage.
4. If worker/daemon/host fails after launch may have occurred—or terminal persistence fails after a possible side effect—mark uncertain and require reconciliation.
5. Never automatically replay uncertain invocations.
6. Keep `max_attempts=1` for side-effecting skills until a skill-specific idempotency contract is proven.

Do not persist raw skill output, raw arguments, credentials or untrusted exception text. Persist bounded status metadata and cryptographic digests.

## 7. Verified gaps in current runner

- `core.coding_task_runner` accepts command argv and workspace, not a manifest or structured skill invocation.
- It sets stdin to `DEVNULL`; bounded protocol stdin requires a deliberate API extension.
- `core.sandbox_runtime` mounts the caller's workspace; invocation-local staging/output collection is missing.
- Approval currently sees image, resolved workspace, command and resource bounds, not the complete actor/workspace/manifest/argument/capability binding above.
- Mainline runtime validates image syntax; Phase 96 PR #405 adds digest enforcement in the coding runner and `--init` / `--ipc=private`, but is stacked on Phase 95 and is not universal mainline behavior.
- Allowlisted networking fails closed without a separate egress gateway.
- Docker argv requires Unix UID/GID support and rejects root service; native Windows is unsupported. Use a separately verified Linux VM/WSL2 backend or fail closed.

Phase 96's capability record explicitly leaves real-Docker adversarial testing, image provenance pipeline and Windows backend verification as release requirements.

## 8. Implementation order

1. Strict manifest parser, canonical serializer, schema validation, digest verification and safe entrypoint validation; no process execution.
2. Extend one-shot approval to bind actor, tenant, manifest/artifact/image, argument digest, capabilities, limits and invocation ID.
3. Add private staging, symlink-safe output collection, explicit grants and bounded cleanup.
4. Extend sandbox API with structured request and bounded stdin/stdout without breaking coding-task callers; preserve fail-closed controls.
5. Add durable adapter with pre-launch intent, audit linkage and uncertain-state recovery.
6. Add adversarial tests: wrong actor, changed digest, replay, traversal/symlink escape, output flood, timeout, fork/PID exhaustion, daemon interruption, cleanup failure, crash between side effect and terminal persistence.
7. Run real-Docker tests on dedicated Linux CI; separately verify Windows VM/WSL2 or explicitly mark unsupported.
8. Obtain independent review of threat model, image supply chain, daemon boundary, audit durability and recovery before enabling autonomous side-effecting skills.

## 9. Non-goals

This contract does not authorize general shell access, arbitrary plugin execution, browser automation, user-secret access, network access or self-modifying code. It does not provision environments, change runtime behavior, enable execution or claim a sandbox is already available.

## 10. Acceptance gate

Keep experimental until manifest/approval tests, isolated staging tests, real-Docker adversarial Linux tests, Windows VM/WSL2 verification (or explicit unsupported status), crash/restart non-replay tests, audit-start/terminal-failure tests, image provenance/signature/scanning and independent security review all pass.

Until then, `DurableSkillWorker` remains in-process and must not be described as sandboxed. General autonomous skill execution remains disabled.
