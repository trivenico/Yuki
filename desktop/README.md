# Yuki Desktop

Windows-first native desktop shell built with Tauri 2.

## Current scope

- Native Tauri window loading the Yuki server on loopback.
- Remote URLs are rejected by the Rust launcher.
- No updater, deep-link handler, shell plugin, global IPC permissions, screen capture, or computer-control permissions are enabled.
- The first milestone expects the authenticated Yuki ASGI service to be started separately.

## Development

Install Rust stable and Node.js, then from this directory:

```powershell
npm install
npm run dev
```

The development server URL defaults to `http://127.0.0.1:8765`. Set `YUKI_DESKTOP_URL` only to a trusted HTTP loopback URL when a different local port is required.

## Build

```powershell
npm install
npm run build
```

The MSI and NSIS bundles are produced by Tauri. This initial shell does not yet bundle or start the Python ASGI service; that requires the shared Yuki application bootstrap and authenticated session handoff.

## Security boundary

Do not expose the server on a LAN interface or set a remote URL. The shell intentionally rejects non-loopback hosts and has no default Tauri IPC permissions. Screen observation, voice capture, notifications, and computer interaction will be added as separately permissioned capabilities after the shared runtime integration.
