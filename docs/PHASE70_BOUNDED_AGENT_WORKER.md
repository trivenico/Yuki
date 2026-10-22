# Phase 70 — Bounded agent worker

This phase adds the first worker interface over the durable task store. The worker runs only explicitly registered Python handlers and never imports a handler name from task data.

## Usage

```python
from yuki_agent_runtime import TaskStore
from yuki_agent_worker import AgentWorker

store = TaskStore("~/.yuki/tasks.sqlite3")
worker = AgentWorker(
    store,
    worker_id="local-worker-1",
    handlers={
        "summarize_text": lambda payload, context: {
            "summary": payload["text"][:500]
        },
    },
)
outcome = worker.run_once()
print(outcome.status, outcome.task_id)
```

Submit an `approval_required=True` task by default and approve it through an authenticated application control before a worker can claim it. The sample handler is illustrative only; production handlers should be normal named functions with their own input validation.

## Boundaries

- The worker invokes registered in-process handlers only; it is not a subprocess sandbox or remote job runner.
- Cancellation and deadline checks are cooperative. Long-running handlers must poll `context.check_cancelled()` and terminate any subprocesses they own.
- The worker records only exception type names, not exception messages, to reduce accidental persistence of secrets or prompt content.
- The task store records token/time budget metadata; provider adapters and handlers must enforce actual usage limits.
- Do not register a handler that blindly executes shell commands, installs untrusted skills, or treats model output/crawled content as trusted instructions.
- Use authenticated authorization for task submission/approval and isolate filesystem/network/tool permissions per handler.
- For multiple hosts, high availability, or strong transactional delivery, replace SQLite with a server database/queue and add migrations, metrics, dead-letter policy and load tests.
