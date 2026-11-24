# WI-6R — Security pipeline worker production hardening

## Failure observability

SecurityPipelineWorker accepts an optional failure_observer callback. It receives only the worker role, stable worker ID, consecutive failure count, bounded retry delay, and the generic code worker_tick_failed. It never receives exception text, traceback, source payload, or credentials. Observer failures are contained and counted locally; they do not alter the worker result or retry path. Successful ticks reset the failure count and last failure code.

Hosts should connect this callback to their metrics/logging system and alert on sustained failures. The callback should emit only the supplied allowlisted fields and must not add request bodies, tokens, principal data, or exception strings.

## Startup rollback

The supervisor now starts each thread before recording it as started. If a later thread fails to start, it requests stop for every configured worker and joins all successfully started threads within a shared 10-second rollback budget. The failed start attempt is terminal for that supervisor instance; construct a new supervisor after investigating the startup failure. Thread references remain available for status inspection and bounded stop.

## Shutdown semantics

stop(timeout_seconds=...) signals every worker and joins started threads against one shared deadline. A blocked operation cannot be interrupted by Python threads; a timeout therefore returns stopped: false for that worker. After the host's bounded database/network operation returns, calling stop() again can confirm termination. Configure I/O timeouts shorter than the host shutdown budget and let the process manager enforce the final process deadline.

## Validation scope

Tests cover sanitized failure observation, observer failure isolation, successful recovery, partial-start rollback, and blocked-operation shutdown reporting. Tests are added but were not executed in this connector-only session. This does not claim process-kill testing, production metrics wiring, or multi-host orchestration.
