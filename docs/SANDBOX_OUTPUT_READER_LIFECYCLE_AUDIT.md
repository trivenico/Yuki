# Sandbox output-reader lifecycle audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: the output-reader thread may outlive the execution call

In `core/coding_task_runner.py`, the runner joins the output-draining thread for two seconds. If it remains alive, the runner closes `process.stdout` and joins for one additional second. It does not check or reject the still-alive condition after the second join. It then performs container cleanup and may return a successful `SandboxExecutionResult`.

A pipe read can remain blocked if another process inherited/retained the pipe's write end. Closing a stream from another thread is not a reliable portable guarantee that a blocked read will immediately terminate. In that situation, the daemon reader thread may outlive the call and continue interacting with the output bytearray / reader error list after the result has been constructed. The exact behavior depends on platform and pipe ownership; this review does not establish that the current Docker CLI deployment leaves such a writer alive.

## Impact and scope

Potential background-thread/resource leakage and non-deterministic output/result finalization. The runner's result may be returned without proving output collection has stopped. This is separate from the output byte cap: the cap limits retained bytes but does not prove reader termination.

## Required remediation

1. Make output collection lifecycle explicit and require reader termination before constructing a result.
2. Prefer a design with deterministic pipe ownership and cancellation, such as a selector/event loop on supported platforms or a dedicated reader process with a bounded shutdown protocol.
3. If shutdown cannot be confirmed, fail with a typed uncertain-output outcome and do not return a normal success result.
4. Ensure cleanup and reader shutdown preserve both primary execution failure and secondary cleanup/reader failures.
5. Add tests where a duplicated/inherited pipe writer keeps stdout open after the Docker client exits; assert the runner never returns success with a live reader and does not mutate result state after return.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
