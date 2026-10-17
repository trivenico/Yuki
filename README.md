# Yuki

Yuki is an early-stage personal AI-agent project designed to combine a personal companion, developer assistance, and defensive cybersecurity tools behind a modular architecture.

**Honest readiness status:** Yuki is not yet a full, production-ready autonomous assistant. The repository contains working foundations and guarded modules, but the real LLM reasoning loop, end-to-end project building, always-on wake-word pipeline, desktop dashboard, and production-hardening are not complete. Do not treat CI passing as proof that all features are production-ready.

## What is implemented now

- `main.py`: guarded terminal runtime wired to the skill registry, explicit approval gate, local memory, reminders, and opt-in voice adapters.
- `yuki.py`: legacy prototype with task/learning records, mock provider, allow-listed tools, and metadata-only audit events.
- `skills/registry.py`: validated registry that rejects duplicate or invalid skill names.
- `companion/memory.py`: local, bounded JSON memory with add/list/delete/clear operations. The CLI asks before writes and destructive changes.
- `companion/proactive.py`: timezone-aware local reminder queue. Due reminders are surfaced only when requested; no background scheduler or arbitrary task execution exists.
- `voice/tts.py`: optional local pyttsx3 output; disabled unless explicitly enabled and approved.
- `voice/stt.py`: optional faster-whisper transcription of a user-selected audio file. It does not open a microphone or detect the wake word.
- `hackback/sandbox.py`: optional Docker command runner disabled by default, requiring a digest-pinned image allow-list and approval. Container isolation is not a guarantee of safety.
- `hackback/recon.py`: optional limited Nmap TCP inventory for a private IP that is explicitly allow-listed and approved. No CIDR scans or hostname targets.
- `hackback/exploit_runner.py`: **fail-closed placeholder only**. Metasploit exploit execution is not implemented and all run requests are blocked.
- Unit tests and GitHub Actions checks.

## What is not complete

- No real Ollama/OpenAI/other LLM tool-calling loop is connected to `main.py`; provider names/config are not proof of an active provider.
- No autonomous project builder that edits code, runs tests, diagnoses failures, creates reviewable diffs, and rolls back changes end-to-end.
- No microphone capture, wake-word detector, live speech conversation, or voice interruption control.
- No graphical or Textual permission dashboard.
- No Gmail/calendar integration, OSINT engine, general security scanner, or working Metasploit integration.
- No production-grade installer, signed release, full threat model, or independent security review.

## Install and run

Requirements: Python 3.10+ and PyYAML.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

The legacy CLI remains available with `python yuki.py`. On Windows, use the included installer scripts if you have permission to run them. Browser-only GitHub cannot run local microphone, Docker, Nmap, or speech-model workloads.

## Guarded runtime commands

- `/help`, `/status`, `/skills`
- `/skill time`, `/skill system`, `/skill files .`
- `/memory add TEXT`, `/memory list`, `/memory delete ID`, `/memory clear`
- `/reminder add ISO_DATETIME | TEXT`, `/reminders`, `/reminders due`
- `/voice status`, `/voice speak TEXT`, `/voice transcribe AUDIO_PATH`
- `/quit`

Each skill and voice action requests explicit approval. Voice remains disabled by default. To enable local TTS/STT, set `YUKI_VOICE_ENABLED=true` in the launch environment and install the optional dependencies:

```bash
python -m pip install -r requirements-voice.txt
```

faster-whisper may download a speech model on first transcription and can require substantial disk space. No microphone listening is implemented.

## Security and privacy

- No arbitrary shell tool is exposed through the agent.
- The security lab is disabled by default and does not provide hack-back, retaliation, persistence, credential theft, or unrestricted exploitation.
- Nmap requires a private literal IP in an explicit allow-list and per-scan approval. Use only assets you own or are expressly authorized to test.
- Metasploit execution is intentionally blocked in this build.
- Memory and reminders are local files under the configured workspace; protect that directory and back it up only if desired.
- Never commit credentials, personal memory, audio files, or real user data.

See [installation](docs/INSTALLATION.md), [project structure](docs/PROJECT_STRUCTURE.md), and [security policy](SECURITY.md).

Yuki is a work in progress. The goal is to earn trust through verified functionality, least privilege, explicit permissions, and testing—not to claim capabilities that have not been implemented.
