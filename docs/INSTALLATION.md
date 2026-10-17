# Install and run Yuki

## Requirements

- Python 3.10 or newer.
- Git, if cloning the repository.
- PyYAML, installed from `requirements.txt`.
- No AI API key is required for the current non-LLM runtime.

## Windows

1. Install Python 3.10+ from https://www.python.org/downloads/ if it is not already installed.
2. Download the repository ZIP and extract it, or clone the repository.
3. Open the repository directory.
4. Run `install_windows.bat`.
5. Run `run_yuki_windows.bat` for the legacy CLI, or run `.venv\\Scripts\\python.exe main.py` for the guarded core.

The installer installs required dependencies and runs the unit tests. It does not install voice models, Docker, Nmap, or an AI model.

## macOS or Linux

```sh
sh install_unix.sh
sh run_yuki_unix.sh
```

Run the guarded core with `.venv/bin/python main.py`.

## Guarded core commands

Use `/help` for all commands. The runtime connects:
- Registered read-only skills: `time`, `system`, `files`.
- Local memory commands: `/memory add TEXT`, `/memory list`, `/memory delete ID`, `/memory clear`.
- Local reminders: `/reminder add 2026-12-31T18:00:00+00:00 | reminder text`, `/reminders`, `/reminders due`.
- Optional voice adapters: `/voice speak TEXT` and `/voice transcribe AUDIO_PATH`.

Memory writes and deletion require confirmation. Reminder times must include a timezone offset. Reminder delivery is checked by the user with `/reminders due`; the application does not run a background scheduler or trigger arbitrary actions.

## Optional voice dependencies

Voice is disabled by default. If you explicitly want local speech output and audio-file transcription, install:

```sh
python -m pip install -r requirements-voice.txt
```

Set `YUKI_VOICE_ENABLED=true` in the process environment before starting Yuki. The speech-to-text adapter uses faster-whisper and may download model weights on first use. The TTS adapter uses pyttsx3 and the available operating-system speech engine. There is no microphone capture, always-on listener, or wake-word detector.

## Tests and diagnostics

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/doctor.py
```

A passing test suite checks only the tested behavior; it does not certify production readiness or verify optional hardware/software that is not installed.

## Security module status

- Docker sandbox: disabled by default; requires an exact digest-pinned image allow-list and per-run approval.
- Nmap recon: disabled by default; requires a private literal IP in an explicit allow-list and per-scan approval.
- Metasploit: execution is not implemented and requests are blocked.
- No hack-back or unauthorized third-party targeting is supported.

## Important limitations

The guarded core does not yet connect to a real LLM, autonomously build projects, or provide a full interactive voice assistant. There is no GUI permission dashboard, microphone listener, Gmail/calendar integration, or production-grade security engine. Do not treat this version as a finished production release or use it for consequential security actions.
