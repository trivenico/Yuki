# Gemini CLI total stdout consumption limit audit

## Finding

**Severity:** P3  
**Area:** Provider resource limits / subprocess I/O  
**File:** `providers/gemini_cli.py`

The Gemini CLI adapter bounds the amount of stdout retained in memory, but does not bound the total amount of stdout consumed before the child exits.

In `GeminiCLIProvider.generate()`, the reader computes:

```python
capture_limit = self.max_output_chars * 4 + 4
```

The `drain_stdout()` worker continuously reads 8 KiB chunks until EOF. It appends only the portion that fits within `capture_limit`, but continues reading and discarding all subsequent bytes. The main thread waits for the child with `process.wait(timeout=self.timeout)`, so elapsed execution time is bounded, but total stdout volume is not.

## Impact

A malfunctioning or unexpectedly verbose CLI process can emit a very large amount of output during the configured timeout. The adapter will continue consuming that output even after it has enough bytes to produce the maximum permitted response.

This can waste pipe I/O and CPU resources and can amplify resource pressure when multiple generations run concurrently. The retained stdout buffer itself remains bounded; this finding is **not** an unbounded-memory claim.

The timeout reduces the exposure window but does not enforce a byte budget. A fast producer can emit a large volume well within the timeout.

## Evidence

The relevant flow is:

1. `capture_limit` limits only the size of `captured`.
2. `drain_stdout()` continues its read loop after `remaining <= 0`.
3. The worker exits only when stdout reaches EOF or a read error occurs.
4. The child is otherwise stopped by the elapsed-time timeout, not by aggregate output volume.

## Recommendation

Enforce a separate maximum total stdout byte count, distinct from the retained response size.

- Track every byte read by the stdout worker.
- When the total exceeds the configured byte budget, signal a limit violation to the caller thread.
- Terminate the process (and process group where supported), reap it, and join both I/O workers.
- Return a sanitized output-limit error rather than treating a truncated prefix as a complete response.
- Keep the retained-output cap as a separate control.
- Add tests for output just below the cap, exactly at the cap, over the cap, and a child that continues writing after the retained buffer is full. Verify the child is terminated and reaped and worker threads exit.

## Scope and confidence

This is a source-level finding based on the subprocess reader and wait lifecycle. No runtime reproduction or tests were executed in this audit. The configured timeout is an existing mitigation, but it does not replace an aggregate byte ceiling.
