# Phase 200 — Local-network device control foundation

## Goal

Yuki should work with devices on the same LAN, including enrolled computers
and phones, through a secure, owner-controlled device-management boundary.
This phase establishes registration and authorization foundations. It does
**not** perform discovery or dispatch remote commands.

Calling and call-session functionality were explicitly removed in PR #715.
Yuki retains voice-assistant speech features, but this device-control roadmap
does not include voice/video calling, call signaling, or media sessions.

## What is implemented

- Reuses the existing one-time, owner-bound device pairing credentials.
- Persists owner-scoped local endpoint registrations in the pairing SQLite DB.
- Requires literal owner confirmation before registering or changing an endpoint.
- Accepts only literal private/link-local IP addresses over HTTPS; DNS names, public IPs,
  loopback, cleartext HTTP, URL credentials, query strings, fragments and arbitrary paths are
  rejected to avoid turning the control plane into an SSRF/DNS-rebinding path.
- Grants only host-defined typed capabilities; remote device metadata cannot
  invent capabilities or authorize arbitrary shell execution.
- Uses revision compare-and-swap when an endpoint or its grants are updated.
- Rechecks the active pairing record when listing and authorizing a device, so
  device revocation immediately blocks subsequent authorization.

## Typed capabilities

The device-control capability vocabulary is limited to approved, non-call
operations such as `device.status`, `device.notify`, `media.play_pause`,
`media.volume`, `display.brightness`, and `device.lock`. Exact grants
remain host-defined and individually authorized. A grant never authorizes a
generic remote command channel.

## Security boundary

The registry returns an authorization record only. It never opens sockets or
executes commands. A future transport must bind each request to the paired
device identity, endpoint revision, capability, expiry and unique request ID;
verify TLS/device identity where applicable; reject replay; enforce rate and
payload limits; and write an audit intent before dispatch. Device revocation
must be checked immediately before dispatch.

A local network alone is not consent or identity. Device control must never
silently activate a microphone or camera, access private device data, or execute
arbitrary shell commands. No broad subnet scanning is permitted; discovery
must be explicit and scoped to an owner-authorized network.

## Not yet production-complete

- mDNS/DNS-SD discovery and explicit subnet-scoped discovery UX.
- Device agents for Windows, macOS, Linux, Android and iOS.
- Authenticated command transport and per-command audit/idempotency.
- Secure installation, update, health/heartbeat, reconnect and offline queueing.
- End-to-end tests across real devices and network isolation/security testing.

No broad subnet scan, arbitrary shell, remote microphone/camera activation, or
unapproved command execution is provided by this phase.
