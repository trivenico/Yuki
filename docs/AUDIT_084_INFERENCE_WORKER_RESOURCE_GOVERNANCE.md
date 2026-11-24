# Audit 084 — Inference Workers, Distributed Execution, and Resource Governance

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** durable inference jobs, paired-device authentication, lease fencing, remote worker transport, local inference worker, worker supervisor, API integration, and related tests.

## Executive summary

Yuki's paired-device inference path has a strong foundation: device credentials are separate from browser sessions; job creation is owner-scoped; devices can only claim their own jobs; lease tokens are random and stored as SHA-256 digests; stale lease tokens cannot complete reclaimed jobs; cancellation clears the active lease; worker model IDs are allow-listed; local inference disables tools; output and HTTP response sizes are bounded; redirects are rejected; and worker loops use capped exponential backoff.

This review identified one confirmed API authentication ambiguity and several queue/resource lifecycle gaps. A focused authentication fix and regression test are submitted separately.

## Findings

### P1 — Inference API collapses duplicate Authorization headers

`AuthenticatedInferenceAPI._bearer` converts ASGI headers into a dictionary. Repeated Authorization fields silently overwrite each other, so different ASGI servers, proxies, or intermediaries can disagree about which credential is authoritative.

**Fix submitted:** require exactly one byte-valued Authorization header and reject missing, duplicate, malformed, or whitespace-containing bearer tokens.

### P1 — Expired inference jobs are requeued without attempt limits or poison-job quarantine

`InferenceJobService.claim_next` changes expired running jobs back to `queued` and immediately makes them eligible for another claim. The inspected schema has no attempt counter, retry budget, next-available timestamp, or dead-letter/quarantine state. A job that repeatedly crashes a worker or consistently fails can be reclaimed indefinitely, consuming worker capacity and creating a hot loop across restarts.

**Recommendation:** track claim/attempt count and last failure class; use bounded exponential retry scheduling; distinguish worker lease expiry from provider-declared permanent errors; move exhausted jobs to a terminal failed/quarantined state; expose owner-visible reason and operator recovery controls. Keep lease-token fencing on every state transition.

### P1 — Cancellation does not preempt active inference execution

The owner cancellation operation marks a queued/running job cancelled and revokes the lease token. This correctly prevents a late worker from committing a result, but `LocalInferenceWorker` performs a synchronous provider call and has no cancellation signal or provider request handle. Cancelling a running job therefore fences its completion but may not stop the compute/network work already in progress.

**Recommendation:** add cancellation/deadline propagation through the worker/provider transport, with an explicit distinction between logical cancellation (result cannot commit) and physical cancellation (computation stopped). Until transports are interruptible, document the residual resource cost and bound provider timeout/output.

### P2 — Lease duration is statically compared with provider timeout, not total execution budget

The local worker validates that lease duration exceeds provider timeout by 15 seconds. This is a useful guard, but it does not account for queue claim/dispatch latency, model startup, local scheduling pauses, serialization, or network completion. The job service has no lease-renewal operation for inference jobs in the inspected interface. A legitimate long-running model may outlive the lease and have its result rejected, while another worker reclaims and duplicates compute.

**Recommendation:** implement authenticated lease renewal with fencing and heartbeat cadence; enforce a total job deadline; size the lease from provider timeout plus bounded overhead; and ensure a worker stops or loses commit authority when renewal fails.

### P2 — Worker supervisor has process-local backoff, not durable retry scheduling

`InferenceWorkerSupervisor` backs off after consecutive loop errors and stops after a bounded number, but its counters and retry state are in memory. A restart resets them. This is appropriate for process supervision but does not replace durable per-job retry policy, queue-level rate control, or fleet-wide backpressure.

**Recommendation:** separate supervisor restart policy from durable job retry state. Add queue admission control, per-device concurrency limits, global worker capacity, and observable queue-age/attempt metrics.

### P2 — Resource governance is narrow and does not establish fleet-wide GPU/CPU admission

The local worker bounds provider count and output bytes and the Ollama provider has a request timeout. The inspected inference job contract does not reserve GPU memory, CPU, VRAM, model residency, or per-device concurrent slots; the worker runs one job per `run_once`, but multiple processes can claim distinct jobs for the same device if operated concurrently.

**Recommendation:** introduce device capability/resource inventory, per-device concurrency leases, resource reservations, model load/unload policy, and global admission control. Enforce limits at the OS/container/device boundary, not only in Python configuration.

### P2 — Remote worker response validation does not fully validate claimed job fields

`RemoteInferenceJobClient.claim_next` checks response keys, basic string types, and lease-token shape, but it does not apply the same job ID/model ID/input length/lease timestamp validation as the server-side job service. The remote worker should treat the authenticated server response as untrusted input at the transport boundary, especially if API gateways or intermediaries are involved.

**Recommendation:** share a strict response schema and enforce field lengths, identifier patterns, timestamp parsing, and payload bounds before handing a claim to provider execution.

### P2 — Inference job retention and result lifecycle are not demonstrated

The job service caps active jobs per owner and list responses, but the inspected contract does not provide terminal-job retention/expiry, result export/deletion, or bounded database growth. Completed and failed job rows may accumulate indefinitely.

**Recommendation:** define owner-visible retention, terminal result TTL, deletion/export, and backup lifecycle. Preserve minimal audit metadata only where required and documented.

## Existing positive controls

- Device credentials are distinct from browser session credentials.
- Owner identity is resolved from browser session, not request body.
- Device pairing is one-time and device revocation is owner-scoped.
- Job creation verifies active device ownership inside a transaction.
- Device claims are restricted to that device's queue.
- Lease tokens are random, hash-stored, rotated on reclaim, and compared in constant time.
- Expired/reclaimed/cancelled leases cannot commit stale results.
- Model IDs are allow-listed by the local worker; tool calls are disabled for this inference path.
- Output, request body, and remote response sizes are bounded.
- Remote API requires HTTPS except loopback, rejects redirects, and sanitizes errors.
- Worker supervisor uses bounded exponential backoff and stops on device credential revocation.
- Worker lifecycle is explicit/pull-based; modules do not silently start daemon threads or listeners.

## Recommended production worker architecture

1. **Durable job state machine:** queued, leased, running, retry-wait, succeeded, failed, cancelled, quarantined.
2. **Lease protocol:** authenticated claim, heartbeat renewal, expiry, token rotation, and compare-and-swap completion.
3. **Retry policy:** max attempts, jittered backoff, permanent/transient error classes, dead-letter/quarantine.
4. **Cancellation:** propagate to interruptible provider transports and process supervisors.
5. **Admission control:** per-owner/per-device quotas, global worker slots, queue age limits, backpressure.
6. **Resource scheduler:** GPU/VRAM/CPU/memory capability inventory and reservation.
7. **Observability:** queue depth, age, attempt count, lease expiry, execution duration, model/device health, sanitized failure class.
8. **Lifecycle governance:** terminal result retention, export/delete, backup expiry, and auditable operator recovery.

## Required regression/adversarial tests

- Duplicate Authorization fields are rejected regardless of order or credential value.
- A job that repeatedly expires is quarantined after the configured attempt budget.
- Retry backoff survives worker process restart.
- A cancelled in-flight provider request is physically stopped where transport supports it; late results remain fenced otherwise.
- Lease renewal rejects stale worker/version/token and cannot resurrect expired leases.
- Concurrent workers cannot exceed per-device resource slots.
- GPU/VRAM admission rejects work before starting provider inference when capacity is unavailable.
- Remote claim response rejects malformed IDs, model IDs, oversized input, invalid timestamps, and malformed lease tokens.
- Terminal job retention and deletion propagate to result storage and backups according to policy.

## Validation limits

Source inspection only. A regression test for duplicate Authorization headers was added in a separate implementation PR but was not executed. No provider execution, real remote device, concurrent multi-process worker, lease-expiry stress, cancellation-under-load, GPU measurement, queue flood, or backup-retention test was run. No claim is made that production GPU scheduling or fleet-wide admission control exists.
