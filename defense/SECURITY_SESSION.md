# Authenticated security session controller

`SecuritySessionController` is the authorization and lifecycle boundary around an injected, read-only desktop security monitor. It does not discover sensors, create workers, or access operating-system APIs.

## Activation contract

- Construct the controller only as part of an authenticated desktop session.
- The owner must obtain explicit, per-session user consent and call `start(consent=True)`.
- The authorization callback must return the literal boolean `True`; exceptions and other values fail closed.
- The monitor's `enable()`, `disable()`, and `snapshot()` methods must be bounded and safe to call from a background worker. `start()`, `stop()`, and `close()` may wait for these methods and therefore must also be invoked off the Qt UI thread. The Qt UI must never call sensor operations directly.
- Snapshots are accepted only when they explicitly declare `enabled=True`, `read_only=True`, `persisted=False`, and `automated_response=False`.

## Cancellation and concurrency

Stop invalidates the active generation and reserves the lifecycle against new starts before calling the monitor's disable method. The reservation remains until cleanup completes, including during authorization revocation. A snapshot that finishes after Stop is discarded. A snapshot failure from an older generation cannot stop a newer session.

If Stop or close races a monitor startup, both operations wait for the late startup to return and its compensating disable to complete before reporting cleanup complete. The controller keeps restart blocked throughout this interval. Stop/disable is also serialized against new starts, so a slow disable cannot turn off a newly started session. Concurrent Stop and revocation requests keep the reservation active until every cleanup request has completed. If disable fails, restart remains blocked until a later Stop successfully retries cleanup. This avoids late enable/disable operations reviving or disrupting a newer session. The controller does not hold its state lock while invoking monitor or authorization callbacks.

The `active` property becomes true only after monitor initialization succeeds; `starting` exposes the initialization phase separately. Call `snapshot()` only from a background worker. The caller should marshal accepted results to the UI thread and independently discard callbacks from obsolete UI/session generations.

## Boundaries

This controller does not itself guarantee that a monitor implementation is read-only; it validates the monitor's declared snapshot contract. The concrete monitor and each platform sensor still require code review and tests proving no persistence, upload, process termination, firewall mutation, or privilege escalation. A failed disable is surfaced as `SecuritySessionError`; the owner must keep the session in a cleanup-required state and retry rather than silently reporting monitoring stopped.

No microphone, camera, packet capture, process inventory, or background monitoring starts by constructing this controller.
