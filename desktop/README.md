# Yuki Desktop

Windows-first native desktop shell built with Tauri 2.

## Current runtime lifecycle

- The Tauri shell starts the local Python ASGI server as a managed child process.
- It uses a fixed loopback address, `127.0.0.1:8765`, and polls `/healthz` before opening the webview.
- Startup has a 30-second deadline. If the child exits or readiness times out, desktop startup fails closed.
- On Windows, the shell assigns the Python process to a Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; shutdown terminates and waits for the direct child, then closing the job also terminates remaining descendants. Other platforms currently stop only the direct child.
- If the configured port is already occupied, startup refuses to attach to an unknown process.
- The desktop loads a bundled copy of the authenticated Yuki web interface from `desktop/dist`; it does not load the UI from the loopback API server. The bundled frontend sends API requests only to the fixed `http://127.0.0.1:8765` origin, and native credential storage remains behind the Tauri command boundary.

## Runtime prerequisites

The current launcher still requires a separately installed Python environment and a Yuki source/application directory. It does not bundle Python, model weights, or Python dependencies into the installer.

Set `YUKI_HOME` to the Yuki application directory containing `api/server.py`. Set `YUKI_PYTHON` to the Python executable in the environment where Yuki's declared dependencies are installed. If `YUKI_PYTHON` is omitted, the launcher uses `python` on Windows and `python3` elsewhere.

For development builds only, the repository root is discovered relative to the Tauri manifest, so the variables can be omitted when dependencies are installed in the default Python environment.

The server is launched with:

    python -m api.server --host 127.0.0.1 --port 8765

Account provisioning and provider configuration remain separate administrative setup tasks. The desktop does not create credentials or enable provider integrations automatically.

## Development

Install Rust stable and Node.js, then from this directory:

    npm install
    npm run dev

The Tauri launcher starts the local server and waits for its health check. Do not start a second server on port 8765 while the desktop owns it.

## Build

    npm install
    npm run build

The MSI and NSIS bundles are produced by Tauri. A successful shell build does not mean the Python runtime or dependencies are bundled. A packaged installation still requires `YUKI_HOME`, a compatible Python environment, and installed Yuki dependencies.

## Security boundary and known limitations

- The server binds only to an IP loopback address; non-loopback bind addresses are rejected.
- The Rust launcher uses a fixed HTTP loopback URL and does not accept remote URLs.
- The process is launched without a shell, with stdin/stdout/stderr disconnected.
- The shell never kills arbitrary processes by name or PID lookup. Windows descendant cleanup is scoped to the Job Object containing the process it launched.
- The health check verifies the local Yuki `/healthz` response, not user authentication or provider readiness.
- The shell has no updater, deep-link handler, shell plugin, screen capture, or computer-control permissions. The bundled frontend is the trusted local webview; the loopback origin is not listed as a remote Tauri capability.
- No notification channel is enabled by this process manager. Companion reminders still require a separately configured, authenticated, owner-scoped delivery adapter.
- Windows Job Object assignment currently occurs immediately after `std::process::Command::spawn`; there is a small race before assignment in which a very early-spawning descendant could escape the job. A suspended native process launch is the follow-up hardening needed to remove that race. Other platforms still need process-group cleanup.
- The current child process logs are suppressed. A privacy-safe rotating diagnostic log with secret redaction should be implemented before broad deployment.

## Next integration milestones

1. Bundle or provision a reproducible Python runtime and dependencies with verifiable provenance.
2. Remove the Windows spawn-to-job-assignment race with a suspended native process launch, then add a supervised, privacy-safe diagnostic log and non-Windows process-group cleanup.
3. Add explicit desktop notification permissions and an owner-scoped reminder channel.
4. Add signed installer/update provenance and release verification.
