# Phase 161 — Supervised local inference worker

## Scope

This phase makes the already-authenticated inference queue executable by a bounded local Ollama worker. It does not enable arbitrary remote commands, tool calls, model downloads, or unreviewed model activation.

## Execution contract

- A worker is explicitly constructed with an InferenceJobService, one paired-device bearer credential, and an operator-configured map of Yuki model IDs to OllamaProvider instances.
- Model IDs in queued jobs must match that allowlist exactly. A job cannot choose an Ollama URL, model installation, shell command, or tool.
- The worker claims one job and uses its opaque, single-claim lease token for completion. The token is excluded from InferenceJob representations and public projections.
- The worker sends only the user prompt to Ollama with an empty tool list and tool_choice="none".
- Provider errors are persisted as stable error codes; exception text, endpoints, credentials and prompts are not copied into job errors.
- Output is bounded before persistence. Oversized or unavailable-model jobs fail with stable error codes.
- The configured job lease must exceed the provider timeout by at least 15 seconds. Deployments with slower models must explicitly configure a longer lease.

## Supervisor lifecycle

InferenceWorkerSupervisor provides a managed run loop with bounded idle polling, capped exponential backoff, a maximum consecutive-error count, immediate stop when device authentication is revoked or rejected, and aggregate counters without prompt or credential data.

The supervisor creates no process, thread, listener or timer. The host must run it inside an explicitly managed service/process and arrange shutdown with a stop event.

## Important boundaries

The queue's lease token fences stale completions after reclaim/cancel, and device validation is serialized with completion and revocation by the shared SQLite transaction introduced in Phase 160. If revocation occurs while inference is running, the result is not accepted; the job remains recoverable through lease expiry.

This is a local Ollama execution adapter, not yet a general remote worker fleet. A production fleet still needs process-level isolation, concurrency limits per device/model, health heartbeats, worker version/capability registration, graceful deployment draining, persistent operational metrics, and end-to-end tests against a real Ollama runtime. Do not expose this worker over an unauthenticated listener.
