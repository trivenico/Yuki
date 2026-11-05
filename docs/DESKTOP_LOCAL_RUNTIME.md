# Yuki Desktop: local runtime integration

The Tauri shell connects to the authenticated Yuki ASGI application at
`http://127.0.0.1:8765`.

## Start the shared application service

Install the Python dependencies, configure a real provider in `config.yaml`
(or the documented environment variables), then run:

```powershell
python yuki_cli.py serve
```

The server binds only to an IP loopback address. Non-loopback bind addresses
are rejected. The default port is 8765 and can be changed with `YUKI_PORT`.

## Create the first administrator

Account creation is deliberately out-of-band. Use the existing
`BuiltInAuthService.bootstrap_admin(username, password)` API from a trusted
local provisioning step. Do not expose an unauthenticated account-creation
HTTP endpoint or commit credentials to the repository.

## Current capability boundary

- Authenticated login and logout are provided by the existing conversation API.
- Conversations use the configured durable storage backend.
- Provider execution uses the real configured provider; no mock provider or
  silent cloud fallback is introduced by this bootstrap.
- The initial application runtime exposes no tools. Its tool policy denies all
  calls and the API supplies an empty tool list.
- Screen observation, voice capture, file access, computer control, workflow
  execution, and remote access remain disabled until each has explicit
  permission, approval, auditing, and tests.

The native shell and Python service are separate processes in this milestone.
A supervised packaged service lifecycle and secure desktop session handoff
remain follow-up work.
