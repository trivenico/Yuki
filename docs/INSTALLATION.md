# Install and run Yuki

## Requirements

- Python 3.10+ and PyYAML.
- No AI API key is needed for the current non-LLM runtime.

## Basic install

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

Windows users can also use `install_windows.bat` and then `.venv\\Scripts\\python.exe main.py`. macOS/Linux users can use `sh install_unix.sh`.

## Model selection and onboarding

On first launch, `python main.py` opens onboarding when `~/.yuki/config.json` does not exist (Windows uses the current user's home directory). Choose **local**, **cloud**, or **hybrid**:

- **Local:** Ollama discovers installed models from the loopback `/api/tags` endpoint. If none are found, enter a model ID after installing one with `ollama pull`. LM Studio and llama.cpp server are also available through compatible local endpoints.
- **Cloud:** select OpenAI, Gemini Developer API, NVIDIA NIM, Anthropic, DeepSeek, GLM, or a custom OpenAI-compatible provider. Choose a suggested model or enter a custom model ID.
- **Hybrid:** configure a local model and optionally a cloud model. Cloud routing remains disabled unless specific task names are explicitly configured.

The desktop app exposes the same provider/model catalog under **Model settings**. Saving settings writes only the provider, model ID, endpoint, and API-key environment-variable name to `~/.yuki/config.json`; it never writes the API-key value. Set credentials in your environment (for example `GEMINI_API_KEY` or `NVIDIA_API_KEY`). The desktop requires confirmation before switching to cloud and before each cloud chat request.

Suggested models are examples, not guarantees of free access. Model IDs, API access, prices, free quotas, and rate limits change; check the provider's own console and terms. You can enter any model ID your account or local server supports. The initial catalog is curated rather than a live inventory of cloud models.

Model discovery for Ollama only contacts loopback HTTP endpoints; remote local-server URLs are not queried by the discovery helper.

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
