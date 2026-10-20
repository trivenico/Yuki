# Yuki Android/mobile support (initial phase)

## Architecture

The Android device is a client; the computer remains the trusted Yuki host and runs Ollama. The initial client is an installable Progressive Web App (PWA), avoiding a second execution environment and making it usable in Android Chrome. A native Kotlin wrapper is intentionally deferred until the API contract and security model have been reviewed.

The first release exposes only:
- Authenticated service health.
- Authenticated local-model inventory.
- Authenticated, text-only inference through the configured local Ollama provider.

It does **not** expose skill execution, shell access, task creation, memory browsing, approval decisions, file access, or remote model/provider configuration. An Android “approve” UI must not be implemented as a direct bypass around Yuki's trusted approval coordinator. Future approval support must display an immutable server-side pending action and call the existing coordinator using a trusted authenticated principal and fresh confirmation.

## Requirements

- Python 3.10+ and the project dependencies installed.
- Ollama installed and running on the computer.
- At least one model already installed in Ollama.
- A long random mobile bearer token (at least 32 characters).
- For remote access: TLS with a certificate trusted by the Android device. Do not expose Ollama's port (normally 11434) directly.

## Run on the computer

Install Yuki and its entry point in the active environment:

```sh
python -m pip install -e .
```

Generate a token without putting it in source control. On Linux/macOS:

```sh
export YUKI_MOBILE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
yuki-mobile
```

PowerShell:

```powershell
$env:YUKI_MOBILE_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(48))"
yuki-mobile
```

The service binds to `127.0.0.1:8765` by default, which is only reachable from the computer itself. To use it from Android on the same trusted LAN, configure a LAN host/IP and TLS certificate/key:

```sh
export YUKI_MOBILE_HOST="192.168.1.20"
export YUKI_MOBILE_PORT="8765"
export YUKI_MOBILE_TLS_CERT="/path/to/fullchain.pem"
export YUKI_MOBILE_TLS_KEY="/path/to/private-key.pem"
yuki-mobile
```

Replace the example address and certificate paths with your own. The certificate must be valid for the hostname/IP used on Android and trusted by Android; a self-signed certificate will not be trusted automatically. Configure your OS firewall to allow only the intended private network. Do not port-forward this service to the public internet. A VPN such as WireGuard/Tailscale with HTTPS or a trusted reverse proxy is preferable for access away from home; keep authentication enabled regardless.

On Android, open `https://<computer-hostname>:8765/` in Chrome, enter the same server URL and token, check the connection, then use the browser menu's **Install app** / **Add to Home screen** action if offered.

## Environment settings

- `YUKI_MOBILE_HOST`: defaults to loopback `127.0.0.1`.
- `YUKI_MOBILE_PORT`: defaults to `8765`.
- `YUKI_MOBILE_TOKEN`: required; never commit or share it.
- `YUKI_MOBILE_TLS_CERT` and `YUKI_MOBILE_TLS_KEY`: required for non-loopback binding.
- Existing `YUKI_OLLAMA_BASE_URL` and `YUKI_OLLAMA_MODEL` settings continue to control local inference.

The mobile service refuses to start unless the configured provider resolves to an available local Ollama provider. It will not quietly return mock answers or route mobile prompts to a cloud provider.

## Security and known limitations

- Bearer-token authentication is required for API endpoints; the token is not stored in browser local storage. It remains in page memory until that tab is closed/refreshed.
- Non-loopback binds require TLS and a configured certificate/key before the listening socket is opened.
- Requests have body/prompt bounds, per-source rate limiting, and generic error responses. These are basic safeguards, not a substitute for a production reverse proxy, stronger identity management, abuse monitoring, or penetration testing.
- Chat responses are untrusted model output and are rendered as text, not HTML.
- Do not share the bearer token in screenshots, chat, logs, or source control.
- The static PWA is served without authentication; it contains no secrets. API routes are authenticated.
- This phase has no multi-user identity, revocation UI, streaming, persistent chat history, voice transport, push notifications, or remote approvals. Do not add those by bypassing the existing security boundaries.
- The API uses the existing model `generate()` method; it is a text-only conversation endpoint, not the bounded skill/task workflow. Model-generated instructions do not execute tools through this endpoint.

## Validation

Run the targeted tests:

```sh
python -m pytest tests/test_mobile_api.py -v
```

Then run the complete repository CI gates. Passing the mobile unit tests alone does not establish overall production readiness.
