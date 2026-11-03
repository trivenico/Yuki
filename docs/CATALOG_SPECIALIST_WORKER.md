# Catalog specialist worker factory

`automation.catalog_specialist_worker.create_catalog_specialist_worker`
provides a small application composition helper. It uses the explicit role
mapping and provider policy from `specialist_provider_wiring`, then constructs
the existing `SpecialistTaskWorker` with handler IDs matching the catalog IDs.

```python
worker = create_catalog_specialist_worker(
    runtime,
    worker_id="specialist-worker",
    service_actor_id="worker-service",
    provider_manager=provider_manager,
    role_map={
        "research": "research",
        "engineering": "coding",
        "testing": "testing",
        "documentation": "reporting",
    },
    provider_preferences={"research": ("ollama",)},
    allow_cloud=False,
)
# The application starts and supervises worker.run_once() or run_concurrently().
```

This helper does not start background work, claim tasks, create a coordinator,
or grant runtime permissions. The application remains responsible for
authorization callbacks, task approval, lifecycle supervision, graceful
shutdown, and registering the worker against the coordinator's catalog IDs.
Provider selection is explicit; cloud access is opt-in.

Run the focused test with:

```bash
python -m unittest tests.test_catalog_specialist_worker
```
