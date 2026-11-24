# WI-5R — Scheduled worker and recovery authorization

## Finding

The bounded WorkerSupervisor previously called TaskRuntime.recover_expired directly using its service actor. In central-authorization deployments, TaskRuntime did authorize that service actor, but the scheduled recovery pass did not first prove that the actual worker workload was authorized for the recovery operation. The supervisor also accepted a centrally authorized worker alongside a TaskRuntime configured with a different gateway (or legacy callback), splitting claim/invocation authorization from task-state authorization.

## Changes

- Strict WorkerSupervisor composition now requires the SpecialistTaskWorker and TaskRuntime to share the exact AuthorizationGateway instance and policy revision.
- A centrally authorized worker cannot supervise a legacy TaskRuntime.
- Before recovering expired leases, strict supervisors ask the worker to authorize `task:recover` through its trusted workload principal and shared gateway. The existing TaskRuntime service-actor authorization remains an independent second gate.
- Recovery authorization happens before the SQLite recovery mutation. Denial or audit failure prevents recovery from changing task state.
- Legacy local/test supervisors remain supported when both runtime and worker are in their existing compatibility configuration.

## Scope and limitations

This closes the bounded specialist WorkerSupervisor recovery path. It does not claim that every external cron, deployment scheduler, inference supervisor, or third-party background process is secured. Those entry points still require separate inventory and composition checks. TaskRuntime's cryptographic workload-credential verifier remains optional in migration configurations; production deployments must configure it and a fresh credential provider for worker task mutations.

Tests were added for gateway consistency and denial-before-recovery. They were not executed in this environment.
