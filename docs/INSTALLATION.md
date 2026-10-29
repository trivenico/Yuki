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

Windows: run `install_windows.bat`, then `run_yuki_windows.bat` for CLI or `run_yuki_desktop_windows.bat` for GUI. Linux: run `sh install_unix.sh`, then `sh run_yuki_unix.sh` for CLI or `sh run_yuki_desktop_unix.sh` for GUI. The launchers run the guided model setup first and then continue to the runtime even if setup is unavailable or declined; no download occurs without consent. Use `python yuki_cli.py desktop` or the platform desktop launcher to open the Tk GUI console. It forwards commands and approval prompts to the same guarded runtime as the CLI. The Windows packaging workflow builds standalone executables, a portable ZIP, and an Inno Setup installer. The Linux packaging workflow builds and smoke-tests a portable x86_64 tarball. Wait for the workflow to pass on the current commit and for release review before using a packaged release. The legacy Textual dashboard remains optional via `requirements-ui.txt`.

## Desktop packages (preview)

The `Windows desktop package` GitHub Actions workflow builds a per-user installer (`Yuki-Setup.exe`) and a portable ZIP. Do not treat these as a release until the workflow has passed on the current PR head and the release is reviewed.

For a validated CI build, open the workflow run in GitHub Actions and download the `yuki-windows-x64` artifact. Run `Yuki-Setup.exe` to install under your LocalAppData Programs folder; administrator privileges are not required. The installer creates Start Menu shortcuts for the desktop GUI and console runtime. The portable ZIP contains the same onedir app folders plus the default configuration and docs.

Ollama remains a separate install and must be running locally with the selected model available. The packaged app copies its bundled default configuration into a writable per-user data directory on first launch and preserves an existing user configuration on subsequent launches. A version tag (`vX.Y.Z`) triggers the Windows GitHub Release containing the installer and portable ZIP after the Windows packaging job succeeds. The Linux tarball is currently uploaded as a CI artifact; Linux release publishing still needs to be wired into the release job.

## Local AI provider (default)

Yuki defaults to local Ollama at `http://127.0.0.1:11434` using model `llama3.2:3b`. Install Ollama separately, then run `python yuki_cli.py setup` to check the exact configured model tag. If it is missing, Yuki asks before running `ollama pull`; declining does not download anything. Setup does not change the selected provider, install Ollama, or claim inference works merely because a model is present. To opt into OpenAI, set `provider: openai` in your local config and provide `OPENAI_API_KEY` via the environment; do not store secrets in YAML or Git. Yuki also supports explicit cloud selection for OpenAI-compatible APIs: `deepseek` (`DEEPSEEK_API_KEY` + `YUKI_DEEPSEEK_MODEL`), `kimi` / Moonshot (`MOONSHOT_API_KEY` + `YUKI_KIMI_MODEL`), `minimax` (`MINIMAX_API_KEY` + `YUKI_MINIMAX_MODEL`), `openrouter` (`OPENROUTER_API_KEY` + `YUKI_OPENROUTER_MODEL`), and `tokenrouter` (`TOKENROUTER_API_KEY` + `YUKI_TOKENROUTER_MODEL`). These adapters use their provider-specific HTTPS endpoints by default; custom endpoints must pass the host trust allow-list. Configure a model explicitly—Yuki will not guess one or silently switch providers. Anthropic is supported through its native Messages API adapter using `ANTHROPIC_API_KEY` and `YUKI_ANTHROPIC_MODEL` (or `anthropic_model` in local config). Hugging Face inference and other non-compatible APIs still need their own adapter work. The desktop GUI forwards operational commands to the guarded CLI runtime instead of implementing a separate execution path.

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

### Web skill

The `web_fetch` skill only fetches HTTPS pages for hostnames explicitly listed under `web_allowed_hosts` in `config.yaml`. An empty allow-list denies all requests. Redirects and private/local IP destinations are blocked.

### Gmail

Install `requirements-email.txt`, configure a Google OAuth client file locally in the workspace, and set `YUKI_GMAIL_ENABLED=true`. OAuth consent may require browser interaction. Gmail access is read-only; sending and changing messages are not implemented. Never commit OAuth tokens or client secrets.

### Dashboard

Install `requirements-ui.txt` and enter `/dashboard` in the guarded runtime. The dashboard displays status and safe controls; sensitive operations remain in the approval-gated terminal.

### Packet inspection

Install `requirements-security.txt` only if you intend to use the finite Scapy packet capture. Capture requires local authorization and may require OS privileges. The IDS emits alerts only.

### Security lab and firewall

Security lab and sandbox are disabled by default. Nmap needs an explicit private IP allow-list and approval for every scan. Docker requires a digest-pinned image allow-list and approval. IDS packet capture is finite and only emits alerts. Firewall rules are **dry-run only** in this release because durable expiry and restart recovery are not yet implemented. Metasploit execution is blocked.

## Commands

Use `/help` for the complete list, including `/plan GOAL`, local memory search, knowledge indexing, reminders, voice, authorized recon, sandbox and defensive alerts.

## Readiness limits

There is no live LLM-driven tool loop, autonomous project builder, always-on voice service, validated speaker recognition, automatic firewall blocking, or production security certification. Do not treat this version as a finished production release.
