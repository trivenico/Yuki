# Phase 226 — Secure provider profile CLI

## Commands

- `yuki provider-profile add --name NAME --endpoint HTTPS_URL --model MODEL --approved-host HOST` prompts for the credential using hidden terminal input; no secret is accepted as an argument.
- `yuki provider-profile list` returns owner-scoped metadata and credential-presence status only.
- `yuki provider-profile select NAME` persists the active profile only after the OS-vault credential resolves and the adapter constructs and reports locally available.
- `yuki provider-profile verify NAME` verifies local vault credential presence and adapter readiness. It does not send a network request or claim remote reachability.
- `yuki provider-profile remove NAME` removes the selected-profile reference, metadata and vault credential. If the vault is unavailable, deletion fails closed and preserves metadata.

## Runtime behavior

The CLI loads the selected profile for both one-shot `ask` and persistent `chat` flows. It uses the shared ProviderManager and OpenAI-compatible adapter. The local CLI owner identity is derived through Yuki's local-owner identity service; owner IDs are never accepted from command-line text.

The profile database is stored below Yuki's private data root at `.yuki/provider_profiles.sqlite3`. API keys are read with `getpass` and sent directly to the OS credential vault. They are not written to YAML, SQLite, command arguments, shell history, output, or audit events. Install the optional extra with `pip install 'yuki-ai-assistant[provider-vault]'` and configure a reviewed native OS keyring backend.

If the keyring backend is unavailable, metadata can still be listed, but credential operations fail closed. A selected profile whose credential cannot be resolved prevents silent fallback to another provider.

## Boundaries

- `verify` is a local readiness check, not an endpoint health check.
- The CLI supports OpenAI-compatible Chat Completions endpoints only. Native provider protocols need dedicated adapters.
- Hostname approval is not complete SSRF protection. Connection-time DNS/IP validation, private-address blocking, redirect rejection and egress controls remain required before arbitrary cloud endpoints are production-enabled.
- The CLI chat runtime continues to deny all local tool execution. Provider management does not grant expanded agent permissions.
- Desktop provider management and the authenticated provider-management API are separate follow-up work.
