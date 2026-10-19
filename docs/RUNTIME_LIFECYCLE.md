# Runtime composition and worker lifecycle

`core/runtime.py` composes the SQLite job queue with `QueuedTaskOrchestrator` and starts a fixed, bounded pool of asyncio worker tasks. The runtime is deliberately separate from `main.py`'s interactive CLI until a later integration change can preserve existing CLI behavior and prove startup compatibility.

## Contract

- Only handlers passed explicitly by trusted application code are registered. No model-generated handler names, shell commands, or arbitrary plugin discovery are enabled by this runtime.
- The existing orchestrator authorizes both submission and execution. A handler must still enforce approvals for each consequential side effect; queue authorization is not a replacement for tool-level permissions.
- `worker_count` is bounded to 1–32. Workers poll with an interruptible idle wait and share the persistent SQLite queue through atomic claims and leases.
- `start()` is single-use; `stop()` is idempotent and stops new claims before waiting up to a configured grace period. The async context-manager interface guarantees that `stop()` is requested when leaving the context.
- Synchronous handlers run in a worker thread so they do not block the asyncio event loop. Every handler must implement network/process timeouts and bounded output. Cancelling the asyncio task cannot forcibly terminate synchronous code already running in a thread; the queue lease is recovery metadata, not a guarantee that external side effects were stopped.
- Errors are logged by the supervisor and can be passed to an optional callback. Callback failures are contained. Applications must redact logs and avoid putting secrets into task payloads/results.

## Not yet wired

This phase does not replace `main.py`, construct providers, enable all skills, schedule autonomous background work, start voice listeners, or claim production readiness. The application composition root should be wired only after its dependencies and current CLI initialization paths are tested. Do not register destructive, financial, network-security, email-send, or code-modification handlers without narrow authorization, audit events, and explicit approval semantics.

## Validation status

Tests were added for bounded configuration, job dispatch, context-manager cleanup, and lifecycle state transitions. They have not been executed in this GitHub-only editing session. Require the project's full test suite and CI on the final PR head before merge.
