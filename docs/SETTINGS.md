# Yuki settings

The desktop console provides a **Settings** window backed by `core.settings_store`. Settings are stored in the active Yuki YAML configuration (`YUKI_CONFIG` when set, otherwise the normal source or per-user packaged configuration path).

## Editable options

- **Local model:** `ollama_model`, default `llama3.2:3b`. Changing this value does not install Ollama or download a model.
- **Voice:** `voice_enabled` and `wakeword_enabled`.
- **Privacy & security:** `security_lab_enabled`, `sandbox_enabled`, `firewall_enabled`, and `firewall_dry_run`.
- **Integrations:** `gmail_enabled` and `plugin_discovery_enabled`.

The settings store permits only these explicit fields, validates model identifiers and boolean types, preserves other configuration values, and writes updates through a temporary file followed by an atomic replace. Malformed YAML or invalid values fail closed without overwriting the existing configuration.

## Apply and override behavior

Restart Yuki after saving settings. Environment variables take precedence over corresponding saved configuration values; for example, `YUKI_OLLAMA_MODEL` overrides `ollama_model`, and `YUKI_VOICE_ENABLED` overrides `voice_enabled`. If the effective value does not change after saving, check the environment used to launch Yuki.

## Safety notes

Enabling a setting does not install optional dependencies, provision credentials, or prove that the feature is available. Firewall dry-run is enabled by default and should remain enabled unless the operator understands the effect of changing it. Sensitive operations retain the runtime's existing authorization and confirmation checks. Plugin discovery should be enabled only when configured Python modules are trusted.

This settings interface is an initial configuration surface, not a production-readiness claim. Provider switching, secure credential management, per-integration connection tests, and restart-free configuration reloads require separate implementations and tests.

## Manual AI provider selection

The desktop Settings window lets the user explicitly select one of the providers currently implemented by the runtime:

- **Ollama** — local by default; requires the Ollama service and selected model to be installed.
- **OpenAI** — cloud API; reads `OPENAI_API_KEY` from the process environment and never stores the key in YAML.
- **Gemini CLI** — uses the separately installed Gemini CLI and its authentication setup.
- **Hybrid** — local Ollama by default; cloud routing requires explicit environment configuration and allow-listed task names.

The provider readiness button calls the configured provider's `is_available()` check and does not send a prompt or download a model. A successful readiness result is not a guarantee that a future generation request will succeed. The user must restart Yuki after saving the selected provider.

This is the runtime's current supported provider list, not a claim that every provider in a model catalog has a working adapter. Other providers should be added only with a real adapter, documented credential handling, capability tests, and an explicit selection entry.
