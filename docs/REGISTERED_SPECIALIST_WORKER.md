# Registered specialist worker lifecycle

Use `RegisteredSpecialistWorker` when a single specialist worker should publish
durable process health. The adapter owns registration and its heartbeat thread,
while the wrapped `SpecialistTaskWorker` continues to own task execution.

```python
registered = RegisteredSpecialistWorker(worker, registry, heartbeat_interval=10)
registered.start()
try:
    while should_continue:
        registered.run_once()
finally:
    registered.close()
```

The adapter registers exactly the wrapped worker ID, heartbeats while the
adapter is alive (including while a task handler runs), and marks the session
stopping then stopped on close. A replacement process session fences the old
adapter; after the old adapter observes that replacement, it refuses to start
another task.

For parallel execution, register each child worker independently. Do not wrap a
pool coordinator and assume its internally-created child worker IDs are covered
by the coordinator's registration.

The registry is observability metadata only. Temporary registry I/O failures
are surfaced in `health_snapshot()` and do not silently alter task state.
Task permissions, task lease renewal, completion fencing, and recovery remain
under the existing TaskRuntime and workflow store.
