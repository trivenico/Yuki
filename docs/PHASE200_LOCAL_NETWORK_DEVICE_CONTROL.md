# Phase 200 — Local-network device control foundation

## Goal

Yuki should be able to work with devices on the same LAN, including enrolled
computers and phones, and support direct device-to-device voice/video calling.
This phase establishes the secure control-plane boundary. It does **not** yet
perform discovery, send remote commands, stream media, or place calls.

## What is implemented

- Reuses the existing one-time, owner-bound device pairing credentials.
- Persists owner-scoped local endpoint registrations in the pairing SQLite DB.
- Requires literal owner confirmation before registering or changing an endpoint.
- Accepts only literal private/link-local IP addresses; DNS names, public IPs,
  loopback, URL credentials, query strings, fragments and arbitrary paths are
  rejected to avoid turning the control plane into an SSRF/DNS-rebinding path.
- Grants only host-defined typed capabilities; remote device metadata cannot
  invent capabilities or authorize arbitrary shell execution.
- Uses revision compare-and-swap when an endpoint or its grants are updated.
- Rechecks the active pairing record when listing and authorizing a device, so
  device revocation immediately blocks subsequent authorization.
- Requires owner confirmation for locking a device and starting/accepting a
  call. Starting a call also requires recipient consent.

## Typed capabilities

`device.status`, `device.notify`, `media.play_pause`, `media.volume`,
`display.brightness`, `device.lock`, `call.start`, `call.accept`, and
`call.end` are the initial host-defined capability vocabulary. A grant only
authorizes the named capability; it is not a generic remote command channel.

## Security boundary

The registry returns an authorization record only. It never opens sockets or
executes commands. A future transport must bind each request to the paired
device identity, endpoint revision, capability, expiry and unique request ID;
verify TLS/device identity where applicable; reject replay; enforce rate and
payload limits; and write an audit intent before dispatch. Device revocation
must be checked immediately before dispatch.

Calling requires a separate signaling and media subsystem (for example,
WebRTC with authenticated signaling, DTLS-SRTP media, explicit microphone and
camera permission, incoming-call acceptance, mute/end controls, and no
background capture). A local network alone is not consent or identity.

## Not yet production-complete

- mDNS/DNS-SD discovery and explicit subnet-scoped discovery UX.
- Device agent for Windows, macOS, Linux, Android and iOS.
- Authenticated command transport and per-command audit/idempotency.
- WebRTC signaling, NAT/LAN traversal policy, media permissions and call UX.
- End-to-end tests across real devices and network isolation/security testing.

No broad subnet scan, arbitrary shell, remote microphone/camera activation, or
automatic call acceptance is provided by this phase.
