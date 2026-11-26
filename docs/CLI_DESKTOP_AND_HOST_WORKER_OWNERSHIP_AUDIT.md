# CLI, Desktop, and Host Worker Ownership Audit (2026-11-26)

## Scope and method

Read-only source review at PR branch `research/reference-projects-integration-assessment`, head `72b8a9fa9c8174d9c507f817ec4659c459ab6922`. Reviewed CLI composition, desktop launcher/lifecycle, ASGI runtime, companion application, worker supervisors, reminder queue, and the checked-in systemd example. This is a source-tree audit, not proof of deployed host configuration. No process was started, no database or user file was opened, and no tests were run locally.

## Confirmed interface ownership

### CLI

- `yuki_cli.py` dispatches finite commands: doctor, setup, provider status/test, one-shot ask, persistent chat, and first-admin bootstrap.
- `yuki chat` constructs `build_local_conversation_runtime()` and closes that runtime in `finally`.
- `core/local_cli_runtime.py` builds the shared conversation application runtime with an empty tool registry and deny-all authorizer. It does not instantiate `WorkerSupervisor`, `InferenceWorkerSupervisor`, `DurableSkillWorker`, or a scheduler.
- `ask` and `provider-test` make direct provider calls; they do not start durable task workers.
- The inspected CLI entry point exposes no command to start a long-running workflow, inference, reminder, or notification dispatcher. This does not rule out external scripts or operator-managed processes outside the repository.

### Desktop

- `ui/desktop/launch.py` owns the Qt process lifecycle and a single-threaded `QThreadPool` used for finite UI operations (provider/conversation work, backup/migration, plan operations, voice playback, logout and shutdown).
- The pool is explicitly limited to one thread. Logout/shutdown are queued behind active work so a runtime is not closed underneath an in-flight desktop operation.
- `DesktopServiceLifecycle` closes its explicitly supplied resources in reverse order, remembers successful closes and retains failed cleanup for retry.
- Desktop conversation runtime closes its conversation store; provider lifecycle remains with the outer desktop process. The authenticated shared desktop bridge closes its conversation session and specialist logout path, not every persistent service in the repository.
- The reviewed launcher does not construct the generic workflow/inference supervisors or a notification outbox dispatcher. Qt worker tasks are finite UI jobs, not evidence of a continuously running durable-task scheduler.
- This is a useful per-process shutdown discipline, but it is not an application-wide maintenance barrier and does not quiesce other CLI/API/host processes.

### ASGI API

- `api/runtime.py` owns an ASGI lifespan handler for the shared conversation application. Shutdown calls `application.close()`.
- The API builder accepts separately injected auth, device-pairing/inference, income, OAuth, MCP and evaluation services. The returned `ConversationRuntime.close()` closes only the shared conversation application; other injected services retain caller-owned lifecycle.
- No worker supervisor, reminder poller, or notification dispatcher is started by this runtime in the reviewed source.
- Any production API host must explicitly own and close each injected service and any separately launched worker process.

## Worker and delivery entrypoints

- `WorkerSupervisor.run_cycle()` is a finite bounded cycle. Its module explicitly creates no thread or scheduler; a host must call it.
- `InferenceWorkerSupervisor.run_forever()` is a loop controlled by an injected stop event. Its module starts no thread/process; the host must own invocation, signal stop, join it, and close the worker's resources.
- `DurableSkillWorker.run_once()` is a finite claim/invoke operation. It creates a daemon heartbeat thread only while one skill invocation is running, then signals and joins it with a timeout. A blocked heartbeat can outlive that join window.
- `SpecialistTaskWorker` runs finite work batches; no persistent host loop was identified in the reviewed composition roots.
- `ReminderQueue.due()` marks due items delivered and persists that state before returning them. A caller that crashes before sending the returned notification can lose delivery; it is not a leased outbox.
- The reviewed repository call paths did not establish a first-party caller for `ReminderQueue.due()`, `ProactiveAlertStore.claim_notification()`, or `settle_notification()`. Therefore no production reminder scheduler, outbox dispatcher, retry policy, or delivery guarantee is established by this audit.
- The checked-in `deploy/systemd/yuki-host-runtime-inventory.service.example` runs only the authenticated host runtime inventory agent. It is not a worker/scheduler/outbox service definition.

## Ownership matrix

| Component | Invocation model | Confirmed owner | Shutdown / delivery status |
|---|---|---|---|
| CLI persistent chat | finite command | CLI process | closes shared conversation runtime |
| Desktop UI jobs | finite Qt thread-pool jobs, one thread | desktop window/process | ordered retryable resource shutdown |
| ASGI conversation API | request/lifespan | API host | closes conversation runtime only |
| Workflow specialist supervisor | finite `run_cycle()` | no repository host found | external host ownership unresolved |
| Inference supervisor | `run_forever()` with stop event | no repository host found | external host ownership unresolved |
| Durable skill worker | finite `run_once()` | no repository host found | heartbeat thread per active invocation |
| Reminder delivery | `due()` claims by marking delivered | no caller found | send-after-mark can lose notifications |
| Proactive notification outbox | explicit claim/settle methods | no dispatcher found | delivery/retry/idempotency unproven |

## Architecture consequences and next controls

1. Do not describe durable workflow, inference, reminder or outbox workers as production-scheduled until a concrete deployment-owned entrypoint, service unit/container definition, health check, stop protocol and restart policy are present and reviewed.
2. Before enabling workers, create one explicit host runtime composition root that owns every worker, scheduler, provider client, database pool/store and notifier. It must start only configured services, expose health/readiness, stop claiming new work on shutdown, drain or fence in-flight work, close all resources, and report incomplete cleanup.
3. Define worker leases and shutdown fencing across processes. A process-local lock or desktop Qt queue cannot stop a second CLI/API/worker process from writing.
4. Change reminder delivery to claim/lease/settle semantics (or document intentional at-most-once loss) before promising delivery. For the outbox, use provider idempotency keys where available and reconcile ambiguous sends; lease expiry alone cannot prevent duplicate external effects.
5. Keep the CLI's current finite-command model and desktop's UI thread pool separate from production background workers. If worker commands are added, make service ownership and explicit stop/health semantics first-class; do not start daemons implicitly from a UI or chat command.
6. Continue the persistent-store manifest only with verified owners. Mark scheduler, worker process and notification dispatcher as `unresolved/external` rather than inventing owners.

## Readiness decision

The source review confirms safe finite CLI/desktop/API lifecycle boundaries, but no in-repository production scheduler or outbox delivery owner for the reviewed durable workers. Host scheduling and notification delivery remain unresolved. Keep unattended worker execution and notification-delivery guarantees disabled until deployment-owned composition, shutdown fencing, delivery idempotency, and integration tests are implemented.
