# Yuki Desktop

Windows-first native desktop shell built with Tauri 2.

## Current scope

- Native Tauri window loads the authenticated Yuki ASGI application from the loopback server.
- The ASGI application serves the existing Yuki web interface at `/`; the desktop does not maintain a second chat frontend.
- Remote URLs are rejected by the Rust launcher.
- No updater, deep-link handler, shell plugin, global IPC permissions, screen capture, or computer-control permissions are enabled.
- The Python ASGI service must currently be started separately. The desktop shell does not yet bundle, launch, or supervise that process.

## Start the local Yuki service

From the repository root, install the declared Python dependencies and start the loopback-only server:

```powershell
python -m pip install -r requirements.txt
python -m api.server --host 127.0.0.1 --port 8765
```

Keep that process running while using Yuki Desktop. The server binds only to an IP loopback address; non-loopback bind addresses are rejected. Account provisioning remains an administrative setup task and is not performed by the desktop shell.

## Development

Install Rust stable and Node.js, then from this directory:

```powershell
npm install
npm run dev
```

The Tauri development window loads `http://127.0.0.1:8765`. Start the Yuki service first using the command above. The launcher accepts only its configured HTTP loopback URL.

## Build

```powershell
npm install
npm run build
```

The MSI and NSIS bundles are produced by Tauri. A successful shell build does not mean the Python runtime is bundled: installation still requires a separately installed and configured Yuki Python environment and a separately started server.

## Security boundary

Do not expose the server on a LAN interface or set a remote URL. The shell intentionally rejects non-loopback hosts and has no default Tauri IPC permissions. Screen observation, voice capture, notifications, and computer interaction will be added as separately permissioned capabilities after shared-runtime integration.

## Integration roadmap

1. Bundle the Python runtime and application assets with verifiable dependency provenance.
2. Launch the service as a managed child process without invoking a shell; verify readiness before opening the webview and terminate only the child process owned by this application.
3. Reuse the authenticated web interface and existing API rather than duplicating chat or memory logic.
4. Add desktop notifications through a permissioned, owner-scoped reminder delivery adapter.
