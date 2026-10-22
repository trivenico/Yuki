# Phase 72 — Supervised worker service

Phase 72 adds a polling service entry point for the durable queue and the provider-backed `model.generate` handler.

## Run once

```bash
YUKI_PROVIDER=mock python -m yuki_worker_service --once
```

## Run continuously

```bash
YUKI_PROVIDER=ollama YUKI_OLLAMA_MODEL=your-local-model python -m yuki_worker_service --poll-interval 1
```

PowerShell example:

```powershell
$env:YUKI_PROVIDER = "mock"
$env:YUKI_TASK_DB = "$HOME/.yuki/agent_tasks.sqlite3"
python -m yuki_worker_service --poll-interval 1
```

Configuration:
- `YUKI_PROVIDER`: `mock`, `ollama`, `gemini`, or `hybrid`; default `mock`.
- `YUKI_TASK_DB`: SQLite task database; default `~/.yuki/agent_tasks.sqlite3`.
- `YUKI_WORKER_ID`: stable worker identity; default includes the process ID.
- `YUKI_OLLAMA_MODEL`: local model name when using Ollama.
- `YUKI_PROVIDER_TIMEOUT_SECONDS`: provider-specific timeout where supported.
- `YUKI_LOG_LEVEL`: log level; default `INFO`.

The service handles SIGINT/SIGTERM and stops after the current handler returns. It logs task IDs and status only, not prompt/result content. Idle polling is bounded to 50 milliseconds–60 seconds.

## Approval and safety

Tasks are approval-required by default in the task store. The service never auto-approves them. Use an authenticated application control to approve tasks before they can be claimed. Do not change approval defaults merely to make the queue appear active.

## Limitations

- A synchronous provider call may delay graceful shutdown until its adapter timeout returns.
- This is a single-process/single-host MVP runner, not a high-availability supervisor. Do not run multiple workers against SQLite without load/concurrency testing and clear lease settings.
- There is no HTTP approval API, authentication server, metrics endpoint, or service manager unit yet.
- Token usage limits are not inferred from budget metadata; provider adapters must enforce actual usage limits.
- For production deployment, add process supervision, health/metrics, structured audit sinks, tested backups/migrations, and a cancellation protocol for subprocess trees.
