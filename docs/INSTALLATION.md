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

## Local CLI authentication (early access)

The terminal CLI includes a local account/session foundation:

```bash
python yuki_cli.py auth bootstrap-admin
python yuki_cli.py auth login
python yuki_cli.py auth status
python yuki_cli.py auth logout
```

The first administrator can only be bootstrapped once. Passwords are entered through a hidden prompt; do not pass passwords as command-line arguments. Local account data is stored under `$YUKI_HOME/.yuki/auth.sqlite3` (or the repository data root when `YUKI_HOME` is unset), and the opaque session token is stored separately with owner-only POSIX permissions where supported.

This is a local CLI sign-in foundation, not yet end-to-end authorization for every companion task, skill, or agent command. The CLI session has not yet been composed into the task-runtime principal resolver or shared with a desktop client. Do not treat local login as a production hosted identity service. The built-in auth module remains framework-independent; hosted deployments still require HTTPS, trusted middleware, CSRF/origin controls where applicable, edge rate limits, account recovery/MFA policy, and secure secret storage. Protect the Yuki data directory and its backups.

## Real AI provider (OpenAI)

Yuki defaults to the OpenAI API using model `gpt-4.1-mini`. Set `OPENAI_API_KEY` in your shell or secret manager before starting Yuki. Do not put the key in `config.yaml`, `.env.example`, source code, or Git. You can override the model with `YUKI_OPENAI_MODEL`; an alternate OpenAI-compatible endpoint requires an explicitly trusted hostname and `YUKI_OPENAI_BASE_URL`. Requests send your prompt to the configured provider. If the key is missing, Yuki reports the provider unavailable rather than fabricating a mock response or silently switching providers. For local inference, configure `YUKI_PROVIDER=ollama`, install/run Ollama, and set `YUKI_OLLAMA_MODEL`.

## Encrypted companion memory (optional)

The default local memory backend stores plaintext JSON and is intended only for non-sensitive preferences. For encrypted storage, install the optional dependencies:

```bash
python -m pip install -r requirements-memory-security.txt
```

Then configure Yuki before starting the CLI:

```bash
YUKI_MEMORY_ENCRYPTION=1
```

On PowerShell, use `$env:YUKI_MEMORY_ENCRYPTION = "1"`. Yuki stores a random 256-bit AES-GCM key in the operating-system keyring and writes authenticated ciphertext to `yuki_memory.enc.json`. The key is not written to the repository or memory file. If a usable OS keyring is unavailable, encrypted mode fails closed; it does not fall back to plaintext. Set `YUKI_MEMORY_PATH` to choose a different data-file location.

This is local-device encryption, not cloud sync or a backup system. Anyone with access to the logged-in OS account and its unlocked keyring may be able to access the data. Back up the keyring and encrypted file together using a separately secured process; deleting the keyring entry makes the ciphertext unrecoverable. Key rotation, cross-device migration, and managed backup/restore are not yet implemented. On Windows, file ACLs inherit from the parent directory; POSIX temporary files are created with owner-only permissions.

## Companion memory controls

In `yuki_cli.py chat`, memory saving and context use are separate session-scoped choices:

- `/memory on`: allow explicit `/remember TEXT` saves for this session.
- `/memory off`: stop saving and stop memory-context use for this session.
- `/memory use on`: separately allow the latest saved notes to inform chat context for this session.
- `/memory use off`: stop including saved notes in chat context.
- `/memory status`: show both consent states.
- `/memories`, `/forget ID`, `/memory export`, `/memory clear confirm`: inspect, delete, export, or clear saved entries.

Both choices default to off after every application restart. Saved notes are bounded and passed as untrusted data, never as policy or tool instructions. A corrupted or unreadable store prevents a context-enabled request from being sent.

## Google Gemini providers

Yuki supports two separate Google integrations. Set `YUKI_PROVIDER=gemini` to use the official Gemini CLI installed and authenticated on your machine. This adapter checks for the CLI executable; it does not claim to verify account authentication until a real request is made.

For direct API access, set `YUKI_PROVIDER=gemini-api`, provide `GEMINI_API_KEY` through your shell or secret manager, and optionally set `YUKI_GEMINI_MODEL` (default: `gemini-2.5-flash`). The direct API adapter uses the Gemini `generateContent` endpoint, sends the API key in the `x-goog-api-key` header, preserves system instructions separately from user/assistant history, rejects redirects, and bounds request time and response size. Never commit the key or put it in `config.yaml`.

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
