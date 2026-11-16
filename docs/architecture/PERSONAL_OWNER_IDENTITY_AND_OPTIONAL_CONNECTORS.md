# Personal owner identity and optional external connectors

## Architecture decision

Yuki is a personal AI assistant. A Google account is **not** Yuki's identity, login, owner record, or startup prerequisite.

The first-party owner identity is created and authenticated by Yuki itself. The existing `BuiltInAuthService` provides local username/password authentication and opaque server-side sessions. Authenticated API handlers derive the owner ID from that Yuki session. Connector callbacks must never select or override the owner identity from callback parameters, email addresses, provider subjects, or model output.

For a single-user installation, the deployment should bootstrap one local administrator and keep account creation closed unless the owner explicitly enables an additional local account. The bootstrap credentials are supplied out of band to the trusted host; they are not Google credentials.

## Optional connectors

Google OAuth is an optional connector used only when the owner explicitly asks Yuki to access a Google service. It is not needed for:

- Starting Yuki or authenticating to Yuki
- Local or cloud model inference
- Yuki memory, conversations, local files, or agent orchestration
- Local workflows and tools that do not require Google data

A Google connection grants only the requested service scopes. The current Gmail provider asks for Gmail read-only and send scopes plus OpenID identity. This is a comparatively broad capability and must remain opt-in; future read-only or send-only profiles should request only the narrower scope actually needed. Google email is not used as Yuki's owner key.

Other connectors (Microsoft, calendar, cloud storage, mail providers, etc.) follow the same rule: separately authorized, least-privilege, owner-scoped, revocable integrations. No provider account should be silently connected or used as a fallback login.

## Startup and trust boundaries

- `scripts/run_web.py` starts with OAuth and MCP disabled unless a complete prepared stack is explicitly injected.
- The default startup path must work without Google client credentials, redirect URIs, Google account access, or an initialized OAuth key store.
- When an optional connector is enabled, the trusted host constructs its provider transport, encrypted token vault, durable transaction store, refresh/disconnect services and registry. All outbound OAuth transports share the exact application firewall instance.
- OAuth encryption keys are initialized explicitly by the operator in the OS credential store. They are not generated automatically by normal Yuki startup.
- OAuth callback state is bound to the existing authenticated Yuki owner and session. Verified external identity identifies the connected provider account only; it does not authenticate the Yuki user.
- Disconnecting a connector revokes/removes that provider grant without deleting the Yuki owner account or local Yuki data.

## Personal-use deployment profile

Recommended default:
1. One local Yuki owner/admin account.
2. Bind the web UI to loopback by default.
3. Keep remote access disabled unless explicitly configured behind trusted TLS and access controls.
4. Keep Google/Microsoft/MCP connectors disabled until the owner connects a specific service.
5. Keep model provider selection independent from connector authentication.
6. Store local memory and files under Yuki's own workspace and apply Yuki's filesystem permissions and backup policy.

This decision keeps Yuki useful offline or with non-Google model providers and avoids coupling a personal assistant to one external identity provider.

## Implementation status

The built-in Yuki authentication service and the web startup's opt-in OAuth injection boundary already exist. The default startup path passes no OAuth or MCP objects. Google-specific authorization code exists as a connector building block, but its full host composition and end-to-end deployment tests remain unfinished. This document is an architecture decision, not a claim that all connector lifecycle paths are production-ready.
