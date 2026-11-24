# WI-6R — Security pipeline runtime hardening

## Changes

- Adds an optional failure observer receiving only the worker role, consecutive failure count, and bounded retry delay. Exception messages, tracebacks, payloads, and credentials are never passed to the observer.
- Observer failures are contained so telemetry cannot prevent retry behavior.
- Tracks total and consecutive failures plus last success/failure timestamps in the in-process supervisor snapshot. These are diagnostic counters, not durable health state; the existing heartbeat and cursor stores remain authoritative.
- Makes supervisor startup rollback explicit: if thread construction or startup fails, all workers receive stop requests, started threads receive a short bounded join, and a generic startup error is raised without exposing implementation details.
- Reports startup failure in supervisor snapshots and shutdown results.
- Shutdown joins only threads that were actually started and reports blocked workers as not stopped rather than claiming success.

## Host integration

The host may connect `failure_observer` to a metrics counter or structured logger. Keep observer implementations non-blocking and emit only the supplied bounded metadata. Do not log exception strings or sensitive operation results. A telemetry backend outage must not break worker retry.

The supervisor cannot forcibly terminate a blocked Python thread. Operations still need bounded I/O and database timeouts. A false `stopped` result is an unclean shutdown and must be handled by the host process manager. The short startup rollback join is intentionally bounded; a stuck operation may outlive it and requires process-level termination.

## Validation

Adds tests for safe failure observations, observer failure isolation, partial startup rollback, and shutdown timeout reporting for a blocked operation. Tests are added but were not executed in this connector-only session.
