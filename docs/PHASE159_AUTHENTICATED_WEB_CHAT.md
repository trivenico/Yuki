# Phase 159 — Authenticated Yuki web chat

## Included

- Responsive browser chat shell at `/` with a login view, recent conversations, conversation history, streaming messages, stop-generation control, and sign-out.
- Same-origin static asset serving through `api.web.YukiWebApplication`; the existing authenticated `/v1` API remains the source of truth.
- Static paths are explicitly allowlisted. User-controlled paths are never joined to the asset directory.
- Security headers include a restrictive same-origin Content Security Policy, `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`.
- Public minimal `/healthz` endpoint for process liveness.
- Local launcher `scripts/run_web.py` and optional ASGI server dependency set `requirements-web.txt`.

## Run locally

Install the base dependencies and optional web server:

```bash
python -m pip install -r requirements.txt -r requirements-web.txt
```

Set a real provider credential (for example `OPENAI_API_KEY`) or configure a supported local provider. OpenAI and other cloud-hosted providers require explicit initial deployment consent: set `YUKI_ALLOW_CLOUD=true` only when you intend to send inference requests to the configured cloud provider. A local Ollama deployment does not require this flag. Interactive cloud switches require the authenticated administrator's explicit confirmation. The browser persists provider ID, exact configured model ID and consent state atomically in `provider-selection.json` (mode 0600); credentials are never stored there. A persisted model/configuration mismatch fails startup rather than silently selecting a different model. On first launch, bootstrap the administrator using environment variables:

```bash
export YUKI_BOOTSTRAP_ADMIN_USERNAME='your-admin'
export YUKI_BOOTSTRAP_ADMIN_PASSWORD='use-a-long-unique-password'
python scripts/run_web.py
```

Open `http://127.0.0.1:8000`. The bootstrap variables are only needed when creating the first administrator. Do not commit them or place them in `config.yaml`. Subsequent launches reuse the existing authentication database.

The launcher binds to loopback by default. Public binding is refused unless `YUKI_WEB_ALLOW_PUBLIC_BIND=true` is set. Only enable it behind a trusted TLS-terminating reverse proxy and add deployment-specific trusted-host validation, request rate limits, access logs, secret management, and network policy. The included launcher is a local MVP entry point, not a hardened internet-facing deployment.

## Safety boundaries

- The initial web profile exposes no tools. Its tool policy denies every invocation; model-proposed tool calls are not authorized by this UI release.
- Authentication remains server-side. The browser keeps the opaque bearer token in `sessionStorage`, not persistent local storage; HTTPS is mandatory outside loopback development.
- The UI uses DOM `textContent` for model and user messages and does not render model-supplied HTML or Markdown.
- API identity is derived from the validated bearer session, never from client-supplied owner IDs.
- Conversation data persists through the configured SQLite or PostgreSQL store. Auth accounts/sessions continue to use the existing SQLite auth service.
- Provider requests require the configured provider and credentials; the launcher does not silently fall back to a mock model.

## Validation and remaining work

Unit tests cover static serving, allowlisted assets, security headers, health endpoint, launcher composition, and deny-by-default tool policy. Before production: run the full CI suite, exercise streaming against each supported provider, add CSRF/origin and trusted-host policy appropriate to the deployment, add login throttling/rate limiting at the edge, validate PostgreSQL with a real service, and perform browser accessibility/security testing. The current interface is a functional MVP shell, not feature parity with commercial AI chat products.
