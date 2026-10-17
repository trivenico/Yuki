# Yuki

Yuki is a modular personal-agent project combining a companion interface, guarded skills, local memory, developer workflows, optional voice, and defensive security-lab foundations.

**Readiness status: not production-ready yet.** There is no live LLM-driven ReAct/tool-calling loop, autonomous project builder, always-on wake-word service, tested custom wake-word model, production firewall auto-expiry/recovery, or independent security review. This repository deliberately reports those gaps rather than implying that module files alone make a finished assistant.


## External SSD portability

Yuki now has portable data/config path resolution and starter launchers for Windows and Linux/macOS. It is not yet a zero-install self-contained executable; compatible Python and optional dependencies are still needed on each host. See [External SSD setup and security checklist](docs/PORTABLE_SSD.md) and [Upstream capability audit](docs/UPSTREAM_CAPABILITY_AUDIT.md).

## Course and interview coaching

The registered `course_coach` skill provides bounded workflows for `explain TOPIC`, `lab TASK`, `quiz TOPIC`, `study_plan GOAL`, and `interview ROLE`. It is a workflow scaffold, not a connected AI tutor: it does not execute lab commands, access course accounts, or submit assessments. Tailored explanations, interactive quiz evaluation, and adaptive interview feedback require a real LLM provider integration.

## Connected capabilities in this revision

- `main.py`: terminal runtime wires the registry, approval gate, local memory/search, bounded planner, knowledge indexer, optional speech, optional read-only Gmail, Nmap, Docker, defensive packet alerts, dry-run firewall and optional Textual dashboard.
- `skills/registry.py`: registers time/system/files, interview practice, allow-listed web fetch/deep-read, local knowledge graph, local health log, defensive remediation suggestions, optional Playwright browser reading, YouTube caption retrieval, and optional read-only Gmail search. Optional integrations fail safely when dependencies are missing.
- `core/planner.py`: creates a bounded inspectable proposal. It does not execute the plan.
- `core/vector_memory.py`: persistent local lexical search over user-approved notes. It is not an embedding/vector database.
- `knowledge/indexer.py`: indexes supported text files explicitly selected inside the configured workspace.
- `knowledge/knowledge_graph.py`: local entities, typed relations, and Obsidian Markdown export. This is explicit CRUD/export; no automatic “dream cycle” is implemented.
- `companion/health_tracker.py`: opt-in local health/habit notes and CSV export. It is not medical advice; medication reminders, streak analytics and proactive health recommendations are not implemented.
- `automation/workflow_engine.py`: bounded allow-listed step runner with approval gates; it does not start schedulers, webhooks, or background jobs.
- `core/prompt_guard.py`: heuristic prompt-injection signal scanning for untrusted browser/deep-read text and MCP tool results. It is not a security boundary or proof that text is safe.
- `core/mcp_client.py`: restricted HTTPS Streamable HTTP MCP client primitive with host/tool allow-lists, explicit per-call approval, bounded JSON responses, and untrusted-result scanning. It is not yet wired to a runtime command/UI, does not launch stdio servers, and does not support SSE-only responses.
- `automation/browser.py` / `skills/browser_skill.py`: optional visible Playwright Chromium page reader restricted to configured HTTPS hosts. It reads page text only; no form submission, arbitrary clicking, downloads, or authenticated browser profile is exposed.
- `skills/youtube_skill.py`: optional caption fetcher for a YouTube video ID/URL when captions are available.
- `skills/image_skill.py`: optional approval-gated OpenAI image generation; requires a user-configured API key and sends the prompt to the remote provider.
- `skills/webcam_skill.py`: optional one-frame webcam capture and remote vision analysis; requires an approved skill call, camera access, and an API key. No background camera listener is started; the captured frame is sent to the remote provider and is not saved by Yuki.
- `voice/stt.py`: opt-in finite recording and faster-whisper transcription; language defaults to automatic detection, and detected language/probability are returned by STT. Recording is only initiated by an explicit command and approval.
- `voice/language.py`: tested language-tag normalization, same-language reply instruction helper, and spoken-reply policy gate. These helpers do not supply an LLM or guarantee detection accuracy.
- Multilingual voice preference defaults: `language_behavior: auto_detect`, `voice_style: natural_realistic_female`, and `spoken_reply_policy: every_reply_when_voice_mode_active`. The current runtime has no conversational LLM response loop, so automatic same-language answers and speaking every generated answer are not yet end-to-end wired. A natural female voice depends on selecting and testing a compatible local voice model; the project does not bundle or claim one.
- `voice/wakeword.py`: optional openWakeWord adapter for a locally supplied model; finite listening is explicit. It does not train a custom model.
- `voice/tts.py`: optional Piper local speech generation/playback, or the pyttsx3 backend. Chunked `speak_stream()` and best-effort `stop()` support interruption of active playback. This is not yet full real-time barge-in: microphone voice activity detection is not wired to stop playback automatically, and no end-to-end latency test has been run. Piper executable and compatible model must be installed separately.
- `voice/speaker_verification.py`: fail-closed placeholder; no real speaker model is claimed.
- `defense/ids.py`: bounded packet capture and port-scan alerts; alerts never automatically change firewall rules.
- `defense/firewall.py`: human-approval boundary and dry-run simulation. **Live firewall mutation is disabled** until durable rule tracking, expiry, and recovery are implemented.
- `hackback/sandbox.py`: opt-in Docker runner with a digest-pinned allow-list, network disabled, read-only root, dropped capabilities, resource limits, and explicit approval.
- `hackback/recon.py`: limited Nmap scan of an explicitly allow-listed private IP with approval.
- `hackback/exploit_runner.py`: fail-closed; Metasploit execution is not implemented.
- `skills/email_skill.py`: read-only Gmail search using user-owned OAuth credentials; sending email is not implemented.
- `ui/dashboard.py`: optional Textual status/supervision interface; sensitive operations remain in the approval-gated terminal.

## Install

Python 3.10+ and PyYAML:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

Optional dependencies are separate:

```sh
python -m pip install -r requirements-voice.txt
python -m pip install -r requirements-ui.txt
python -m pip install -r requirements-email.txt
```

Voice needs `YUKI_VOICE_ENABLED=true`, a local Piper executable/model (if using Piper), and a supported audio device. The STT call defaults to automatic language detection; accuracy depends on the installed faster-whisper model and audio quality. Piper voices are model-specific and are not automatically female or natural-sounding. Choose a legally obtained model that matches the desired voice/language and test it locally before treating that language/voice as supported. Wake-word detection also requires `YUKI_WAKEWORD_ENABLED=true` and a local compatible model file configured in `config.yaml`. No continuous listener runs in the background. faster-whisper can download model weights on first use.

Web fetch requires explicit hostnames in `web_allowed_hosts`; an empty list denies all requests.\n\nGmail is disabled by default. To opt in, set `YUKI_GMAIL_ENABLED=true`, create your own OAuth client file locally, and complete Google's OAuth flow. Token files must remain private. The integration is read-only.

The Textual dashboard is launched from the guarded runtime with `/dashboard`. Browser-only GitHub cannot access local microphones, run Docker/Nmap, or run local voice models.

- `companion/evolution.py`: opt-in local daily review cycle. It analyzes only explicitly recorded, bounded observations, redacts common secret-like strings, produces review proposals, and requires human review. It does not edit code/config, train a model, access arbitrary files, call a network, or execute actions.

## Daily evolution cycle (opt-in)

Yuki does not silently inspect conversations or learn from every task. Record concise observations deliberately, then run a review:

```text
/evolution record failure | CI failed because a test fixture was outdated
/evolution record lesson | Run unit tests before pushing
/evolution record feedback | Prefer short status summaries
/evolution status
/evolution run
/evolution review PROPOSAL_ID accept
/evolution review PROPOSAL_ID reject
```

The cycle creates `yuki_workspace/yuki_evolution_report.json`; review decisions only record accept/reject and do not apply changes. Evolution files are ignored by Git. The secret redactor is best-effort, not a guarantee—never enter credentials or sensitive health data.

For an unattended local daily cycle, explicitly start this process and leave the computer running:

```sh
python -m companion.evolution --daily --hour 3 --workspace ./yuki_workspace
```

This runs at 03:00 local time while the process remains alive. It is not an OS service and does not wake a sleeping computer; stopping the process stops scheduling. `--once` runs one cycle immediately. No scheduler starts automatically. The daily cycle only proposes review items; it does not perform self-modification.

## Runtime commands

- `/help`, `/status`, `/skills`, `/skill NAME [ARG]`
- `/plan GOAL` — proposal only, no automatic execution
- `/memory add TEXT`, `/memory list`, `/memory search QUERY`, `/memory delete ID`, `/memory clear`
- `/knowledge index RELATIVE_PATH`
- `/reminder add ISO_DATETIME | TEXT`, `/reminders`, `/reminders due`
- `/voice status`, `/voice record SECONDS`, `/voice transcribe AUDIO_PATH`, `/voice speak TEXT`, `/voice wakeword status`, `/voice wakeword listen SECONDS`
- `/security status`, `/security recon PRIVATE_IP`, `/sandbox status`, `/sandbox run IMAGE | ARGS`
- `/defense status`, `/defense sniff`, `/defense block IP`, `/defense remove RULE_ID`
- `/metasploit status`, `/dashboard`, `/quit`

## Security and privacy

- Security, microphone access, Gmail and firewall controls are opt-in; approval callbacks fail closed.
- The firewall implementation is dry-run only; do not assume that a host has been blocked.
- The IDS alerts on suspicious port patterns but does not independently block an IP.
- Use recon and sandbox features only on systems you own or are explicitly authorized to test.
- No hack-back, retaliation, unrestricted exploit execution, credential theft, or arbitrary host shell skill is supported.
- Local memory, reminders, recordings, OAuth credentials, and tokens should never be committed to Git.

See [installation](docs/INSTALLATION.md), [project structure](docs/PROJECT_STRUCTURE.md), and [security policy](SECURITY.md).

Yuki is still under active engineering. Passing CI verifies tested code paths, not production readiness or optional local hardware/software.
