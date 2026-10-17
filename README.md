# Yuki

Yuki is an early-stage personal AI-agent project designed to combine a personal companion, developer assistance, and defensive cybersecurity tools behind a modular architecture.

**Honest readiness status:** Yuki is not yet a full, production-ready autonomous assistant. The repository contains working foundations and guarded modules, but the real LLM reasoning loop, end-to-end project building, always-on wake-word pipeline, desktop dashboard, and production-hardening are not complete. Do not treat CI passing as proof that all features are production-ready.

## What is implemented now

- `main.py`: guarded terminal runtime wired to the skill registry, explicit approval gate, local memory, reminders, opt-in voice adapters, and optional security modules.
- `yuki.py`: legacy prototype with task/learning records, mock provider, allow-listed tools, and metadata-only audit events.
- `skills/registry.py`: validated registry that rejects duplicate or invalid skill names.
- `companion/memory.py`: bounded local JSON memory with add/list/delete/clear. The CLI confirms writes and destructive actions.
- `companion/proactive.py`: timezone-aware reminder queue; due reminders are surfaced only when requested, never used to trigger arbitrary actions.
- `voice/tts.py`: optional local pyttsx3 speech output.
- `voice/stt.py`: optional faster-whisper transcription of a selected audio file; no microphone or wake-word detector.
- `hackback/sandbox.py`: optional Docker runner disabled by default, requiring a digest-pinned image allow-list and approval.
- `hackback/recon.py`: optional limited Nmap inventory restricted to an explicitly allow-listed private IP and approval.
- `hackback/exploit_runner.py`: fail-closed placeholder; Metasploit exploit execution is not implemented.
- Unit tests and GitHub Actions checks.

## Not complete yet

- No real Ollama/OpenAI/other LLM tool-calling loop is connected to `main.py`.
- No end-to-end autonomous project builder that edits code, runs tests, fixes failures, presents diffs, and rolls back.
- No microphone capture, wake-word detector, live speech conversation, or voice interruption controls.
- No graphical/Textual permission dashboard.
- No Gmail/calendar integration, general OSINT engine, or production-grade security scanner.
- No working Metasploit integration or independent security review.

## Install and run

Python 3.10+ and PyYAML are required.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

The legacy CLI remains available with `python yuki.py`. Browser-only GitHub cannot run local microphone, Docker, Nmap, or speech-model workloads.

## Guarded core commands

- `/help`, `/status`, `/skills`
- `/skill time`, `/skill system`, `/skill files .`
- `/memory add TEXT`, `/memory list`, `/memory delete ID`, `/memory clear`
- `/reminder add ISO_DATETIME | TEXT`, `/reminders`, `/reminders due`
- `/voice status`, `/voice speak TEXT`, `/voice transcribe AUDIO_PATH`
- `/security status`, `/security recon PRIVATE_IP`
- `/sandbox status`, `/sandbox run IMAGE | ARGS`, `/metasploit status`
- `/quit`

Each skill, voice, scan, or sandbox action requests approval. Voice, recon, and sandbox are disabled by default. To use optional speech adapters, install `python -m pip install -r requirements-voice.txt` and set `YUKI_VOICE_ENABLED=true` in the launch environment. faster-whisper may download a large speech model on first transcription. No microphone listening is implemented.

## Security and privacy

- No arbitrary host shell tool is exposed through the agent.
- Nmap requires a private literal IP in an explicit allow-list and per-scan approval. A private IP is not proof of authorization.
- Docker execution requires an exact digest-pinned image allow-list, explicit opt-in, and approval. Containerization is not a complete security boundary.
- Metasploit execution is intentionally blocked in this build.
- Never implement hack-back, retaliation, persistence, credential theft, or unrestricted exploitation.
- Memory and reminders are local files under the configured workspace. Protect the directory and never commit secrets, personal memory, or private audio.

See [installation](docs/INSTALLATION.md), [project structure](docs/PROJECT_STRUCTURE.md), and [security policy](SECURITY.md).

Yuki is a work in progress. The goal is to earn trust through verified functionality, least privilege, explicit permissions, and testing—not to claim capabilities that have not been implemented.
