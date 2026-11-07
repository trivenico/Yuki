# Phase 268 — Controlled local verification executor

## Delivered

Added `automation/local_verification_executor.py`.

The executor:
- Accepts only command IDs present in a trusted, application-configured allowlist.
- Takes no caller-provided argv at execution time.
- Executes argv directly with `shell=False`, no stdin, and a minimal inherited environment.
- Runs with the configured workspace as its working directory.
- Enforces a per-command timeout (0.1–900 seconds).
- Drains stdout and stderr concurrently and enforces a combined output cap.
- Terminates the process when timeout or output limits are exceeded.
- Persists immutable execution receipts with command ID, exact argv, workspace,
  start/end timestamps, exit code, timeout/output-limit flags, output digest,
  byte count, bounded excerpt, and normalized status.
- Exposes receipt lookup by execution ID.
- Converts a receipt into a `VerificationCheck`; only an actual zero-exit
  `passed` receipt can produce status `passed`. Timeout, output cap, and
  launch errors become `blocked`; nonzero exit becomes `failed`.

Added `tests/test_local_verification_executor.py` covering allowlist rejection,
successful execution, nonzero exit, timeout, output cap, durable receipt
persistence, configuration validation, and receipt-to-check conversion.

## Security boundaries and limitations

This is a bounded subprocess runner, not an OS sandbox. The process runs with
the current user's OS permissions and can access files allowed to that user.
A working-directory setting alone does not prevent path traversal or network
access. Production deployment should additionally use OS-level isolation:
unprivileged account, container/namespace or platform sandbox, read-only source
mount where practical, isolated writable temp directory, no network by default,
and OS-enforced CPU/memory/process limits.

The command allowlist must be constructed by trusted application configuration.
Never populate it from model output, task descriptions, or user-supplied argv.
A command configured by trusted code can still be dangerous if its executable
or arguments are unsafe; review allowlist entries as security-sensitive code.

Execution receipts are stored separately from verification reports. The helper
creates a report check referencing a receipt, but report ingestion does not yet
resolve and independently authenticate that receipt. The next integration must
verify receipt existence, digest, status, and command policy before accepting
the report.

This phase does not yet wire a default test command into `main.py` or the local
runtime. No tests have been executed in this environment and no CI result is
claimed.
