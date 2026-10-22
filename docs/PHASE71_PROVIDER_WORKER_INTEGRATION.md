# Phase 71 — Provider-backed worker tasks

Phase 69 provides durable task state and Phase 70 provides a worker that invokes allowlisted handlers. Phase 71 connects the configured provider to that worker using the task type `model.generate`.

## Example

```python
from yuki_agent_runtime import TaskStore
from yuki_agent_worker import AgentWorker
from yuki_provider_worker import create_provider_handlers
from yuki_providers import ProviderManager

manager = ProviderManager(config={"provider": "mock"})
handlers = create_provider_handlers(manager)
store = TaskStore("~/.yuki/tasks.sqlite3")

# A user-facing app should require authenticated approval before this claim.
store.submit(
    "model.generate",
    {"prompt": "Explain the difference between unit and integration tests."},
    approval_required=True,
    max_attempts=2,
    budget_seconds=60,
)
worker = AgentWorker(store, worker_id="provider-worker", handlers=handlers)
# Approve through the application's authenticated approval control first.
# outcome = worker.run_once()
```

The handler resolves the selected provider from trusted application configuration. Task payloads cannot override the provider, model, endpoint, executable, command, or tool list. Prompts and responses have explicit size bounds, and the worker checks cancellation/deadlines before and after the provider call.

## Important limitations

- This is synchronous, in-process provider execution. A blocked provider call cannot be forcibly interrupted by the cooperative task context. Network adapters must use timeouts; native CLI adapters need process-tree termination and bounded output capture.
- Token budget metadata is not a token meter. Provider adapters must measure/enforce usage where supported.
- A task's output is stored in the task result; callers must apply their data retention and access policies.
- Provider selection is fixed when handlers are constructed. Rebuild handlers only through an authorized, explicit provider-selection flow.
- This does not expose shell execution, dynamic imports, browser control, or arbitrary tools.
- Keep approval enabled for cloud inference or any task with external side effects. Task approval must be authorized by the application; setting an `approved_by` string alone is not authentication.
- Run the tests with `python -m unittest discover -s tests -p 'test_yuki_provider_worker.py'`.
