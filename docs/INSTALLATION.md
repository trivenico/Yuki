# Install and run Yuki

## Requirements

- Python 3.10+ and PyYAML.
- For real AI responses, configure an OpenAI API key in your process environment. The mock provider is test-only and is no longer offered by the runtime provider registry.

## Install from a source checkout

Python 3.10+ is required. The project now declares a stable `yuki` console command:

```sh
python -m pip install .
yuki setup
yuki doctor
yuki provider-status
yuki ask "Hello, Yuki"
yuki chat
```

The first installed invocation creates a non-secret user configuration from the packaged defaults. Configuration is stored in the OS user config directory; runtime data is stored in the OS user data directory. Set `YUKI_CONFIG` or `YUKI_HOME` to override those locations. API keys remain environment variables or OS-managed secrets. `yuki chat` stores conversations in the configured local SQLite workspace and reopens the most recent conversation on the next run. Use `/new`, `/list`, `/use ID`, and `/history` inside chat. The CLI stores a private local owner-scope identifier; it is not a network login credential. CLI tool execution remains disabled, and chat requires a provider that supports Yuki structured agent conversations (currently OpenAI-compatible or Ollama).

For a full source checkout and contributor test environment:

```sh
python -m pip install -r requirements.txt
python -m pip install .
python -m unittest discover -s tests -v
```

The Windows and macOS/Linux installer scripts create a virtual environment, install the declared dependencies and project package, then run the test suite. Use `run_yuki_windows.bat` or `./run_yuki_unix.sh` to start the installed CLI.

## Oracle Cloud Linux VPS (Ubuntu 24.04 ARM64)

The initial deployment target is one private Oracle Cloud VM configured by the operator: Ampere A1 Flex, 4 OCPUs, 24 GB RAM, Ubuntu Server 24.04 LTS ARM64, and the storage allocation shown in the OCI Console. Treat the Console as authoritative for the instance's actual allocation and billing eligibility; inspect boot and attached volumes rather than assuming all displayed storage is one mount.

Before installing packages, clone the reviewed repository and run the read-only preflight:

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python3 scripts/oracle_vps_preflight.py
python3 scripts/oracle_vps_preflight.py --json
```

The preflight makes no network requests, installs no packages, and changes no files. It checks Linux, Ubuntu 24.04, ARM64, Python 3.10+, Git, venv support, and the selected target directory. It reports observed CPU, memory, and disk facts but does not impose a universal RAM minimum. A passing result is not a security certification and does not account for every OCI/network policy or cgroup limit.

Install Yuki into an isolated environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
yuki --help
python -c "import yaml, yuki_cli; print('Yuki CLI imports OK; PyYAML', yaml.__version__)"
yuki doctor
yuki worker status
```

Keep application data outside the Git checkout and private to the Unix account:

```sh
mkdir -p "$HOME/.local/share/yuki"
chmod 700 "$HOME/.local/share/yuki"
printf '\\nexport YUKI_HOME="$HOME/.local/share/yuki"\\n' >> "$HOME/.bashrc"
```

Start a new login shell after setting `YUKI_HOME`. Review `yuki worker status` after installation to see effective CPU and memory observations. Do not configure parallel workers or a local model until measured per-worker/model memory use supports it. The minimal CLI profile does not require CUDA, a GPU, PyTorch, or a local LLM; remote inference is optional and requires explicit provider configuration and consent to send prompts to that provider.

### SSH and network safety

- Use SSH public-key authentication and restrict the OCI NSG/security-list inbound TCP 22 rule to your trusted source IP/CIDR.
- Do not expose Yuki's CLI, worker, database, or development ports to the public internet.
- Before enabling UFW, inspect existing rules and allow the actual SSH port. Keep the current SSH session open and verify a second SSH login before closing it.
- Do not disable password authentication or edit SSH daemon settings until key-based login and an OCI Console recovery path have both been verified.
- Do not run Yuki as root. Keep provider credentials in environment variables or an OS secret manager, never in YAML or Git.
- Inspect storage with `lsblk -f` and `df -h`. Keep the OS/application on the boot volume and use a separately mounted encrypted data volume for large datasets only if one is actually attached and configured.
- Back up private Yuki data and verify a restore before relying on it.

This is an operator-guided first deployment, not an unattended installer. No deployment, firewall changes, provider setup, or background service is performed by the preflight.

## Guided provider setup

Run `yuki setup` to choose OpenAI, Ollama, or Gemini and save non-secret provider preferences. OpenAI and Ollama ask for a model name; Gemini uses the installed CLI's own model/authentication configuration. Setup never requests or stores API keys. It checks existing YAML for credential-like field names before rewriting it and refuses to modify a configuration containing such fields; move credentials to environment variables or an OS secret manager first.

The setup command writes configuration atomically to the selected user configuration path and applies owner-only permissions on POSIX systems. From a source checkout, setup writes to the OS user configuration directory by default and deliberately refuses to modify repository files. Set `YUKI_CONFIG` only when you want a different configuration path. For OpenAI, set `OPENAI_API_KEY` separately in your shell or secret manager. After setup, run `yuki provider-status`; run `yuki provider-test` only when you consent to sending its fixed probe to the selected provider.

## Verify a real provider request

`yuki provider-status` performs a local readiness check only. To verify that the configured provider can accept a request and return a response, explicitly run:

```sh
yuki provider-test
```

This sends the fixed, non-sensitive prompt `Reply with exactly: YUKI_CONNECTION_OK` to the configured provider. The response text is discarded; Yuki prints only a sanitized success/failure report. A successful check verifies one request at that moment, not future availability, quota, billing status, or every model capability. The request may incur provider charges and sends the probe prompt to the selected provider. Do not run it if you do not consent to that transfer.

## Real AI provider (OpenAI)

Yuki defaults to the OpenAI API using model `gpt-4.1-mini`. Set `OPENAI_API_KEY` in your shell or secret manager before starting Yuki. Do not put the key in `config.yaml`, `.env.example`, source code, or Git. You can override the model with `YUKI_OPENAI_MODEL`; an alternate OpenAI-compatible endpoint requires an explicitly trusted hostname and `YUKI_OPENAI_BASE_URL`. Requests send your prompt to the configured provider. If the key is missing, Yuki reports the provider unavailable rather than fabricating a mock response or silently switching providers. For local inference, set `YUKI_PROVIDER=ollama`, install/run Ollama, and set `YUKI_OLLAMA_MODEL` to an installed model name.

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
