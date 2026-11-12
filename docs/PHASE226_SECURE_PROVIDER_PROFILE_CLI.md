# Phase 226 — Secure provider profile CLI

## Commands

- `yuki provider-profile add --name NAME --endpoint HTTPS_URL --model MODEL --approved-host HOST`
  prompts for the credential using hidden terminal input; no secret is accepted as an argument.
- `yuki provider-profile list` returns owner-scoped metadata and credential-presence status only.
- `yuki provider-profile select NAME` persists the active profile for the local owner only after the vault credential is available and the adapter constructs.
- `yuki provider-profile verify NAME` verifies local vault credential presence and adapter construction. It does not send a network request or claim that the remote endpoint is reachable.
- `yuki provider-profile remove NAME` removes the selected-profile reference, metadata and vault credential. If the vault is unavailable, deletion fails closed and preserves metadata.

## Runtime behavior

The CLI loads the selected profile for both one-shot `ask` and persistent `chat` flows. It uses the same ProviderManager and OpenAI-compatible adapter as the application runtime. The local CLI owner identity is derived through the existing local-owner identity service; the profile store never accepts an owner ID from command-line text.

The profile database is stored below Yuki's private data root at `.yuki/provider_profiles.sqlite3`. API keys are read with `getpass` and sent directly to the OS credential vault. They are not written to YAML, SQLite, command arguments, shell history, output, or audit events. Install the optional extra with `pip install 'yuki-ai-assistant[provider-vault]'` and ensure a secure OS keyring backend is configured.

If the keyring backend is unavailable, metadata can still be listed, but add/select/verify operations fail closed. A selected profile whose credential cannot be resolved prevents silent fallback to another provider.

## Boundaries

- `verify` is a local readiness check, not an endpoint health check.
- The CLI supports OpenAI-compatible Chat Completions endpoints only. Native Anthropic/Gemini/Hugging Face APIs need dedicated adapters.
- The endpoint host must be explicitly approved. Hostname approval is not a complete SSRF defense; the network transport still needs DNS/IP pinning or connection-time destination validation, private-address blocking and redirect rejection.
- The CLI chat runtime continues to deny all local tool execution. This phase adds provider management, not expanded agent permissions.
- No desktop provider manager or authenticated provider-management API is included in this phase.
