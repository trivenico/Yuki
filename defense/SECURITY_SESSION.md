# Authenticated security session controller

`SecuritySessionController` is the authorization and lifecycle boundary around an injected, read-only desktop security monitor. It does not discover sensors, create workers, or access operating-system APIs.

## Activation contract

- Construct the controller only as part of an authenticated desktop session.
- The owner must obtain explicit, per-session user consent and call `start(consent=True)`.
- The authorization callback must return the literal boolean `True`; exceptions and other values fail closed.
- The monitor's `enable()`, `disable()`, and `snapshot()` methods must be bounded and safe to call from a background worker. The Qt UI must never call sensor operations directly.
- Snapshots are accepted only when they explicitly declare `enabled=True`, `read_only=True`, `persisted=False`, and `automated_response=False`.

## Cancellation and concurrency

Stop invalidates the active generation before calling the monitor's disable method. A snapshot that finishes after Stop is discarded. A snapshot failure from an older generation cannot stop a newer session.

If Stop or close races a monitor startup, the controller keeps restart blocked until the late startup returns and a compensating disable completes. Stop/disable is also serialized against new starts, so a slow disable cannot turn off a newly started session. If disable fails, restart remains blocked until a later Stop successfully retries cleanup. This avoids late enable/disable operations reviving or disrupting a newer session. The controller does not hold its state lock while invoking monitor or authorization callbacks.

Call `snapshot()` only from a background worker. The caller should marshal accepted results to the UI thread and independently discard callbacks from obsolete UI/session generations.

## Boundaries

This controller does not itself guarantee that a monitor implementation is read-only; it validates the monitor's declared snapshot contract. The concrete monitor and each platform sensor still require code review and tests proving no persistence, upload, process termination, firewall mutation, or privilege escalation. A failed disable is surfaced as `SecuritySessionError`; the owner must keep the session in a cleanup-required state and retry rather than silently reporting monitoring stopped.

No microphone, camera, packet capture, process inventory, or background monitoring starts by constructing this controller.
