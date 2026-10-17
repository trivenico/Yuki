# Install and run Yuki

## Requirements

- Python 3.10 or newer.
- Git, if cloning the repository.
- PyYAML, installed from `requirements.txt`.
- No AI API key is required for the current non-LLM runtime.

## Windows

1. Install Python 3.10+ from https://www.python.org/downloads/ if needed.
2. Download the repository ZIP and extract it, or clone the repository.
3. Open the repository directory.
4. Run `install_windows.bat`.
5. Run `run_yuki_windows.bat` for the legacy CLI, or `.venv\\Scripts\\python.exe main.py` for the guarded core.

## macOS or Linux

```sh
sh install_unix.sh
.venv/bin/python main.py
```

## Commands in the guarded core

Use `/help` for the complete list:
- Skills: `/skills`, `/skill time`, `/skill system`, `/skill files .`
- Memory: `/memory add TEXT`, `/memory list`, `/memory delete ID`, `/memory clear`
- Reminders: `/reminder add 2026-12-31T18:00:00+00:00 | reminder text`, `/reminders`, `/reminders due`
- Voice: `/voice status`, `/voice speak TEXT`, `/voice transcribe AUDIO_PATH`
- Security: `/security status`, `/security recon PRIVATE_IP`, `/sandbox status`, `/sandbox run IMAGE | ARGS`, `/metasploit status`

Memory changes and sensitive actions require confirmation. Reminder times must include a timezone offset. Reminders are surfaced when you ask; they do not run background actions.

## Optional voice dependencies

Voice is disabled by default. To use local speech output and audio-file transcription:

```sh
python -m pip install -r requirements-voice.txt
```

Set `YUKI_VOICE_ENABLED=true` in the launch environment before starting Yuki. faster-whisper may download model weights on first use. No microphone capture, always-on listener, or wake-word detector exists yet.

## Security configuration

Security features are disabled by default. For Nmap, set `security_lab_enabled: true` and list exact private lab IPs under `recon_allowlisted_targets` in `config.yaml`; every scan still requires approval. For Docker, set `sandbox_enabled: true` and list exact image references pinned by digest under `sandbox_allowed_images`; every run still requires approval. Use only systems you own or are authorized to test. Metasploit execution is not implemented and requests are blocked.

## Tests and diagnostics

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/doctor.py
```

A passing test suite checks only tested behavior; it does not certify production readiness or verify optional hardware/software not installed.

## Limitations

The guarded core does not yet connect to a real LLM, autonomously build projects, or provide a full interactive voice assistant. There is no GUI permission dashboard, microphone listener, Gmail/calendar integration, general OSINT engine, or production-grade security engine. Do not treat this version as a finished production release.
