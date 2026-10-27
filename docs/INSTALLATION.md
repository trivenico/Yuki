# Install and run Yuki

## Requirements

- Python 3.10+ and PyYAML.
- Ollama installed separately, running locally, with the configured model pulled for local inference. Cloud providers are opt-in and require their own credentials.

## Basic install

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python yuki_cli.py start
```

Windows: run `install_windows.bat`, then `run_yuki_windows.bat`. macOS/Linux: run `sh install_unix.sh`, then `sh run_yuki_unix.sh`. Use `python yuki_cli.py desktop` or the platform desktop launcher to open the Tk GUI console. It forwards commands and approval prompts to the same guarded runtime as the CLI; it is not yet a standalone packaged executable. The legacy Textual dashboard remains optional via `requirements-ui.txt`.

## Local AI provider (default)

Yuki defaults to local Ollama at `http://127.0.0.1:11434` using model `llama3.2:3b`. Install Ollama separately and run `ollama pull llama3.2:3b` before first chat if the model is not already present. No model download or prompt is sent automatically during installation. To opt into OpenAI, set `provider: openai` in your local config and provide `OPENAI_API_KEY` via the environment; do not store secrets in YAML or Git. The desktop GUI forwards operational commands to the guarded CLI runtime instead of implementing a separate execution path.

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
