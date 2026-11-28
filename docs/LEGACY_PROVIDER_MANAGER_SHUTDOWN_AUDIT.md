# Legacy terminal provider shutdown audit

Date: 2026-11-28

## Finding

The root-level `yuki.py:main()` creates a `ProviderManager`, creates the selected
adapter, and enters the input loop. Normal `/quit`/EOF/interrupt exits break
out of the loop, but the function does not call `ProviderManager.close()`.
The provider manager therefore relies on process teardown rather than its
explicit adapter lifecycle API.

This matters even though the terminal is a compatibility prototype: provider
adapters may own HTTP clients, subprocesses, sockets, or other resources.
Explicit close is the intended ownership boundary.

## Existing manager contract

`ProviderManager.close()` is designed to be retried safely:

- it marks the manager as closing and rejects new work;
- it refuses to close while adapter request leases are active;
- it closes manager-owned selected, retired, and routed adapters outside the
  lifecycle lock;
- it continues attempting other adapters after one close failure;
- successfully closed adapters are removed from manager ownership;
- failed adapters remain owned so a later `close()` can retry;
- the manager becomes fully closed only when every owned adapter closes.

A caller must therefore not discard the manager or its dependencies after a
close failure. A simple unconditional one-shot cleanup that suppresses the
exception would hide a resource leak and remove the opportunity to retry.

## Required main-loop behavior

The terminal host should wrap provider construction, initial memory/task load,
and the input loop in a lifecycle `try/finally` so that failures during startup
also trigger cleanup once a manager exists. Cleanup must:

1. call `providers.close()` after the input loop has stopped;
2. report a bounded cleanup warning if close fails, without printing provider
   exception details or credentials;
3. preserve the original startup/runtime exception rather than masking it
   with a secondary close failure;
4. not claim successful shutdown when the manager reports active leases or
   failed adapter closure.

The current synchronous terminal loop does not intentionally leave provider
requests running in background threads. If future commands add asynchronous or
background requests, the host must first drain/join those operations and then
retry manager close; it must not forcibly invalidate active adapter leases.

## Scope and safety boundary

This is lifecycle cleanup, not a fix for the legacy terminal's broader
execution-surface concerns. The legacy durable task and skill-approval SQLite
stores remain source-relative and outside the canonical private-profile
maintenance fence. The root-level terminal still exposes skill invocation and
durable worker commands; do not use it for consequential or unattended actions
until those authorization, profile, isolation, and recovery contracts are
addressed.

No provider request was sent, no adapter was started by this audit, and no
persistent user data was read, changed, or migrated. This document records a
source-level finding and implementation requirement; it does not claim the
shutdown fix is implemented.
