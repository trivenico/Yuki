# Phase 172 — Paired compute-worker admission

## Delivered

- Add an owner-scoped, process-local compute-worker capability registry.
- Require a valid device bearer credential through the existing
  `DevicePairingService` before admitting any capability record.
- Require the advertised worker ID to equal the paired device ID.
- Derive paired state from successful credential authentication rather than
  trusting the worker's self-reported `paired` flag.
- Keep worker enablement owner-controlled; worker advertisements cannot
  re-enable a disabled worker.
- Provide owner-scoped scheduler records and explicit registry removal.
- Enforce a configurable per-owner worker cap.

## Security boundary

The registry is in-memory and process-local. It does not provide network
transport, persistent capability storage, credential rotation, remote command
execution, signed job envelopes, or sandboxing. The host application must
authenticate the owner before calling owner-management methods and must use
TLS or mutually authenticated transport for remote credentials. Do not expose
bearer credentials to logs or job payloads.

A worker must send fresh authenticated heartbeats; scheduler freshness is
measured from server receipt time, not the worker's claimed timestamp.
