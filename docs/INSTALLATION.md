# Install and run Yuki

## Requirements

- Python 3.10+ and PyYAML.
- For real AI responses, configure an OpenAI API key in your process environment. The mock provider is test-only and is no longer offered by the runtime provider registry.

## Basic install

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

Windows users can also use `install_windows.bat` and then `.venv\\Scripts\\python.exe main.py`. macOS/Linux users can use `sh install_unix.sh`.

## Real AI provider (OpenAI)

Yuki defaults to the OpenAI API using model `gpt-4.1-mini`. Set `OPENAI_API_KEY` in your shell or secret manager before starting Yuki. Do not put the key in `config.yaml`, `.env.example`, source code, or Git. You can override the model with `YUKI_OPENAI_MODEL`; an alternate OpenAI-compatible endpoint requires an explicitly trusted hostname and `YUKI_OPENAI_BASE_URL`. Requests send your prompt to the configured provider. If the key is missing, Yuki reports the provider unavailable rather than fabricating a mock response or silently switching providers. For local inference, configure `YUKI_PROVIDER=ollama`, install/run Ollama, and set `YUKI_OLLAMA_MODEL`.

## Free-tier provider APIs

Yuki supports direct Google Gemini Developer API and NVIDIA NIM API adapters in addition to the official Gemini CLI and local Ollama. Free access is subject to each account's model availability, rate limits, regional eligibility, and provider terms; it is not an unlimited or guaranteed production quota. Check the provider console before relying on a quota. Review provider privacy terms before sending private source code or business data.

### Google Gemini Developer API

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. In PowerShell, set the key for the current terminal session: `$env:GEMINI_API_KEY = "your-key"`.
3. Set `provider: gemini_api` in `config.yaml` (or use the existing `create_provider({"provider": "gemini_api"})` path). The default model is `gemini-2.5-flash`; override with `YUKI_GEMINI_MODEL`.

### NVIDIA NIM API

1. Generate an API key from [NVIDIA Build](https://build.nvidia.com/).
2. In PowerShell, set `$env:NVIDIA_API_KEY = "your-key"`.
3. Set `provider: nvidia` in `config.yaml`. The default model is `openai/gpt-oss-20b`; override with `YUKI_NVIDIA_MODEL`. Hosted model IDs and trial limits can change; use a model currently enabled for your account.

Never commit API keys or put them in YAML. These adapters do not automatically fall back to another cloud provider on authentication errors, quota exhaustion, or network failures. For local-first routing, use `provider: hybrid` with explicit cloud task allow-list settings; verify the router configuration before using it with sensitive data.

## Optional integrations

Install only what you intend to use:

```sh
python -m pip install -r requirements-voice.txt
python -m pip install -r requirements-ui.txt
python -m pip install -r requirements-email.txt
```

### Voice

Set `YUKI_VOICE_ENABLED=true` in the process environment. Voice is otherwise disabled. Piper requires a separately installed executable and compatible model file configured with `tts_backend: piper` and `piper_model_path`. Set `YUKI_WAKEWORD_ENABLED=true` and configure `wakeword_model_path` for finite wake-word listening. The app does not create/train a custom model; training requires suitable consented audio data and the detector's training workflow.

Commands: `/voice record SECONDS`, `/voice transcribe AUDIO_PATH`, `/voice speak TEXT`, `/voice wakeword status`, `/voice wakeword listen SECONDS`. Each recording/listening action requires approval. There is no always-on background listener.

### Web skill\n\nThe `web_fetch` skill only fetches HTTPS pages for hostnames explicitly listed under `web_allowed_hosts` in `config.yaml`. An empty allow-list denies all requests. Redirects and private/local IP destinations are blocked.\n\n### Gmail

Install `requirements-email.txt`, configure a Google OAuth client file locally in the workspace, and set `YUKI_GMAIL_ENABLED=true`. OAuth consent may require browser interaction. Gmail access is read-only; sending and changing messages are not implemented. Never commit OAuth tokens or client secrets.

### Dashboard

Install `requirements-ui.txt` and enter `/dashboard` in the guarded runtime. The dashboard displays status and safe controls; sensitive operations remain in the approval-gated terminal.

### Packet inspection\n\nInstall `requirements-security.txt` only if you intend to use the finite Scapy packet capture. Capture requires local authorization and may require OS privileges. The IDS emits alerts only.\n\n### Security lab and firewall

Security lab and sandbox are disabled by default. Nmap needs an explicit private IP allow-list and approval for every scan. Docker requires a digest-pinned image allow-list and approval. IDS packet capture is finite and only emits alerts. Firewall rules are **dry-run only** in this release because durable expiry and restart recovery are not yet implemented. Metasploit execution is blocked.

## Commands

Use `/help` for the complete list, including `/plan GOAL`, local memory search, knowledge indexing, reminders, voice, authorized recon, sandbox and defensive alerts.

## Readiness limits

There is no live LLM-driven tool loop, autonomous project builder, always-on voice service, validated speaker recognition, automatic firewall blocking, or production security certification. Do not treat this version as a finished production release.
