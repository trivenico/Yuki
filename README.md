# Yuki

Yuki is a modular personal-agent project combining a companion interface, guarded skills, local memory, developer workflows, optional voice, and defensive security-lab foundations.

**Readiness status: not production-ready yet.** A bounded JSON-based model-guided task loop is now present and requires fresh approval for every registered-skill invocation; it is not a production-grade native tool-calling/ReAct runtime. There is no autonomous project builder, always-on wake-word service, tested custom wake-word model, production firewall auto-expiry/recovery, or independent security review. This repository deliberately reports those gaps rather than implying that module files alone make a finished assistant.



## Shared application runtime

The CLI now composes its provider manager, AI agent and durable conversation store through `core/application_runtime.py`. This is the first shared service boundary intended for both the Windows desktop interface and the Oracle Linux service. Interfaces should call `YukiApplicationRuntime.create()`, `send_message()`, `new_conversation()`, `switch_conversation()` and `close()` rather than constructing their own provider or conversation services.

This is an incremental foundation, not a completed desktop/cloud client-server architecture. The runtime currently executes in-process and has a bounded authenticated loopback WSGI API for desktop-client integration. A native Tkinter desktop UI and thread-confined runtime worker are now present as an early source-run preview; remote RPC and a production server launcher remain subsequent milestones.

## Native desktop shell (early preview)

A first native Tkinter desktop chat shell is available in `yuki_desktop.py`. On Windows, run `start_yuki_desktop.bat`; it creates an isolated `.venv`, installs `requirements.txt`, and starts the app. Python 3.10+ with Tcl/Tk support is required. On systems with Tk installed, run `python yuki_desktop.py`.

The UI supports chat, local conversation history, new conversations and switching between saved conversations. The UI thread never accesses SQLite or providers: `core/desktop_worker.py` creates and owns the application runtime on a dedicated worker thread and exchanges bounded commands/results with the UI.

Yuki stores its data under the current user's platform data directory by default (Windows LocalAppData, macOS Application Support, Linux XDG data home or ~/.local/share/Yuki). Set `YUKI_HOME` to choose another data root. This prevents an installed app from trying to write its database into a read-only installation directory.

The desktop settings row lists registered AI providers, distinguishes implemented adapters from planned integrations, and lets you switch between available adapters. It also displays the selected provider's configured model, privacy classification, readiness reason and whether a credential is configured (never its value). This is metadata-only; it does not probe remote providers or enumerate their model catalogues. Provider changes and catalog reads are handled by the shared runtime worker.

The desktop now renders provider output incrementally and includes a Stop control. Ollama and OpenAI-compatible adapters use native bounded streaming; adapters without a native streaming implementation use the backward-compatible complete-response fallback. Stopping closes the active stream and deliberately leaves the user message in history without recording partial assistant output as a completed answer. This is still an early source-run preview, not a signed installer or production release. Voice, remote Oracle Cloud connection controls, automatic sync, secure updater, crash recovery, and a packaged desktop installer remain outstanding. Oracle Linux remains a headless service/CLI deployment; it does not need a graphical desktop environment.

## Minimal project structure and navigation

Keep the repository easy to navigate by giving each responsibility one clear home. The current codebase still has legacy root-level Python modules; they are kept in place during the first cleanup pass to avoid breaking imports, scripts, and Windows launchers. **Do not bulk-move files until the import graph and packaging tests are in place.**

### Target layout

```text
Yuki/
├── yuki.py                 # stable CLI entry point (temporary compatibility facade)
├── config.yaml             # non-secret defaults only
├── requirements.txt        # minimal runtime dependencies
├── src/yuki/
│   ├── app.py              # application composition and lifecycle
│   ├── agent/              # orchestration and model interaction
│   ├── providers/          # Ollama and cloud provider adapters
│   ├── memory/             # memory, retrieval and persistence
│   ├── tasks/              # durable task state and workers
│   ├── skills/             # skill contracts, registry and reviewed skills
│   ├── security/           # policy, approvals, audit and isolation
│   ├── tools/              # bounded tool interfaces and implementations
│   ├── interfaces/cli/     # terminal UX
│   ├── interfaces/desktop/ # desktop UX
│   └── config/             # configuration schema and validation
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── acceptance/
├── scripts/                # setup, diagnostics and smoke tests
├── docs/                   # setup, architecture and operations
├── assets/                 # static UI assets only
└── data/                   # ignored runtime data; never committed
```

This is the target layout, not a claim that the migration is already complete.

### Rules

1. One shared application/service layer powers both CLI and desktop. UI code must not duplicate model routing, memory, task orchestration, or security policy.
2. Provider adapters live together behind one contract. Timeouts, response limits, safe HTTP handling, and normalized errors are shared behavior.
3. Approval, audit, workspace containment, and tool policy each have one authoritative implementation.
4. Skills are bounded capabilities, not independent hidden agents. They cannot bypass approvals or spawn arbitrary shell/code execution.
5. `config.yaml` contains defaults only. API secrets stay in environment variables or the OS credential store. User data, databases, logs, model downloads, and generated artifacts stay in a configured user-data directory outside source control.
6. Tests mirror the architecture: fast unit tests, deterministic integration tests, security tests, and opt-in acceptance tests for real Ollama/cloud calls.
7. Keep root files limited to stable entry points, project metadata, and essential config. Put scripts in `scripts/`, architecture/operations docs in `docs/`, and static resources in `assets/`.
8. Avoid speculative top-level folders, duplicated roadmaps, one-file-per-phase modules, and unused dependencies. Every package must have a clear owner and purpose.
9. Root-level legacy modules remain compatibility facades during migration. Move one domain at a time, update imports, add regression tests, and remove old paths only after clean-install CI passes.

### Safe cleanup order

1. Inventory tracked files, import edges, entry points, generated data, duplicates, and unused modules.
2. Add reproducible packaging and repository-wide syntax/static checks before moving code.
3. Migrate configuration and provider contracts first; retain compatibility imports.
4. Migrate agent/runtime, memory/tasks, tools/skills, and security one domain at a time.
5. Make CLI and desktop call the same application layer.
6. Reorganize tests without losing coverage; validate Windows and Linux clean installs, real Ollama inference, persistence, and rollback.
7. Remove compatibility facades only when import search and CI prove no supported consumer relies on them.

## Windows + Oracle Cloud synchronization

A feature-branch foundation is being built for connecting a Windows laptop to a Yuki Sync Hub on Oracle Cloud Linux. It includes an allow-listed event contract, SQLite event store, authenticated API primitives, one-time pairing, rotating credentials, and a shared cross-platform client/outbox. It is **not yet an install-and-sync release**: the CLI/desktop still need to enqueue and apply real application records, the background worker and Windows credential-vault integration need end-to-end validation, and the Linux service still needs hardened deployment. See [Windows + Oracle Cloud sync status and setup](docs/WINDOWS_OCI_SYNC.md). The optional Windows OS credential-vault dependency is listed in `requirements-sync.txt`.

### Sync CLI commands (feature branch)

The terminal frontend exposes `/sync status`, `/sync pair`, `/sync tasks preview|queue`, `/sync chat preview|queue`, `/sync skills preview|queue`, and `/sync run`. Configure `YUKI_SYNC_URL` with the HTTPS hub URL and install the optional OS credential vault dependency from `requirements-sync.txt`. Pairing prompts for the one-time code without echoing it. Queue commands explicitly add allow-listed records to the local outbox; they do not upload immediately. The sync cycle currently applies task upserts, conversation metadata/messages, and data-only skill-pack metadata. It does not sync executable plugins, approval grants, preferences, deletions, or message/skill edits. Imported skills are forced to high risk and still require local approval. This is a development foundation, not a production deployment. See `docs/WINDOWS_OCI_SYNC.md`.

## External SSD portability

Yuki now has portable data/config path resolution and starter launchers for Windows and Linux/macOS. It is not yet a zero-install self-contained executable; compatible Python and optional dependencies are still needed on each host. See [External SSD setup and security checklist](docs/PORTABLE_SSD.md) and [Upstream capability audit](docs/UPSTREAM_CAPABILITY_AUDIT.md).

## Course and interview coaching

The registered `course_coach` skill provides bounded workflows for `explain TOPIC`, `lab TASK`, `quiz TOPIC`, `study_plan GOAL`, and `interview ROLE`. It is a workflow scaffold, not a connected AI tutor: it does not execute lab commands, access course accounts, or submit assessments. Tailored explanations, interactive quiz evaluation, and adaptive interview feedback require a real LLM provider integration.

## Conversational roles and trusted skill plugins

Yuki includes a `role` skill with `list`, `current`, and profile names such as `companion`, `tutor`, `interviewer`, `developer`, `security`, and `marketing`. These profiles guide interaction style only; they never grant tool permissions.

A controlled plugin loader is available in `core/plugin_loader.py`. Plugin discovery is **disabled by default** in `config.yaml`. To enable discovery after reviewing the code, set `plugin_discovery_enabled: true`. With `trusted_plugin_modules: []`, no external module is trusted; add exact module names (without `.py`) to that list. For a reviewed plugin at `skills/my_helper.py`, add `my_helper`. If you specifically want future `.py` files auto-discovered without listing each one, set `trusted_plugin_modules: ["*"]` only after reviewing every Python file in `skills/`: this explicitly trusts all modules in that package, including future files, and can execute their code with Yuki's permissions. Keep discovery disabled unless you accept that risk.

A plugin must define a concrete subclass of `skills.base.Skill`, implement `run(self, argument="")`, and provide a unique simple `name`, `description`, and safe input validation. The loader imports the module and instantiates the class with no arguments. Plugin imports execute Python with Yuki's permissions: this is **not a sandbox**. Never trust downloaded or user-supplied plugin code without reviewing it. Duplicate skill names are skipped; discovery failures do not crash core startup.

## Ruflo-inspired capability infrastructure

- `skills/discovery.py` is a discovery prototype that scans skill modules and reports import failures. It is **not wired into the default registry** because importing arbitrary Python executes it with Yuki's permissions; a reviewed plugin trust policy is required first.
- `core/capabilities.py` exports registered skill descriptions as JSON for future UI use. It does not create a web dashboard.
- `core/token_usage.py` records local session token counts, distinguishes estimates from exact provider metadata, and reports threshold/spike alerts. It is not yet connected to Ollama or a real LLM provider, so no actual model usage is being monitored yet.
- `core/mcp_client.py` supports allow-listed HTTPS MCP discovery and approved tool calls, but it is not connected to the runtime command/registry/UI flow. Discovery never grants permission to execute a tool.
- See [Ruflo adoption audit](docs/RUFLO_ADOPTION_AUDIT.md) for limitations and security requirements.

## Connected capabilities in this revision

- `yuki.py`: terminal runtime wires the registry, approval gate, local memory/search, bounded planner, knowledge indexer, optional speech, optional read-only Gmail, Nmap, Docker, defensive packet alerts, dry-run firewall and optional Textual dashboard.
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
python yuki.py
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

- `/help`, `/status`, `/providers`, `/provider select NAME`, `/dashboard`
- `/skill list`, `/skill install PATH`, `/skill inspect NAME`, `/skill approve NAME [argument]`, `/skill invoke NAME [argument]`
- `/durable goal create`, `/durable task submit KEY | GOAL_ID | SKILL | ARGUMENT`, `/durable task list GOAL_ID`, `/durable task cancel TASK_ID`, `/durable run`
- `/plan GOAL` — proposal only, no automatic execution
- `/memory add TEXT`, `/memory list`, `/memory search QUERY`, `/memory delete ID`, `/memory clear`
- `/knowledge index RELATIVE_PATH`
- `/reminder add ISO_DATETIME | TEXT`, `/reminders`, `/reminders due`
- `/voice status`, `/voice record SECONDS`, `/voice transcribe AUDIO_PATH`, `/voice speak TEXT`, `/voice wakeword status`, `/voice wakeword listen SECONDS`
- `/security status`, `/security recon PRIVATE_IP`, `/sandbox status`, `/sandbox run IMAGE | ARGS`
- `/defense status`, `/defense sniff`, `/defense block IP`, `/defense remove RULE_ID`
- `/metasploit status`, `/dashboard`, `/quit`

### Portable Agent Skills imports

Yuki accepts its existing JSON skill-pack format and portable `SKILL.md` files with YAML frontmatter:

```text
/skill install /path/to/SKILL.md
/skill list
/skill inspect SKILL_NAME
```

Imported Markdown is converted to Yuki's data-only skill-pack format. Third-party skills default to high risk and require the normal one-shot invocation approval. Frontmatter such as `allowed-tools`, `command-tool`, and `command-dispatch` is descriptive only: it does not grant tools, shell access, network access, or execution permissions. Imported skill instructions are untrusted content; no Python or shell code is imported or run. License and author metadata are retained for provenance.

See [upstream capability audit](docs/UPSTREAM_CAPABILITY_AUDIT.md) and [third-party notices](THIRD_PARTY_NOTICES.md) for reuse decisions and license attribution.

Yuki also bundles an adapted Hermes Agent root-cause debugging skill. From a repository checkout, install it with:

```text
/skill install skills/upstream/hermes/systematic-debugging/SKILL.md
```

It is installed as high-risk guidance and requires the normal one-shot approval before invocation.

## Security and privacy

- Security, microphone access, Gmail and firewall controls are opt-in; approval callbacks fail closed.
- The firewall implementation is dry-run only; do not assume that a host has been blocked.
- The IDS alerts on suspicious port patterns but does not independently block an IP.
- Use recon and sandbox features only on systems you own or are explicitly authorized to test.
- No hack-back, retaliation, unrestricted exploit execution, credential theft, or arbitrary host shell skill is supported.
- Local memory, reminders, recordings, OAuth credentials, and tokens should never be committed to Git.

See [installation](docs/INSTALLATION.md), [project structure](docs/PROJECT_STRUCTURE.md), [provider routing](docs/PROVIDER_ROUTING.md), and [security policy](SECURITY.md).

Yuki is still under active engineering. Passing CI verifies tested code paths, not production readiness or optional local hardware/software.


## Real provider and approved task-loop milestone

The provider layer now defaults to the real OpenAI API and includes a local Ollama adapter, official Gemini CLI adapter, and explicit hybrid router. The mock adapter is retained only for automated tests and is not exposed by the runtime provider registry.

- Set `OPENAI_API_KEY` in the process environment; the default model is `gpt-4.1-mini` and can be overridden with `YUKI_OPENAI_MODEL`.
- Set `YUKI_PROVIDER=ollama` and configure `YUKI_OLLAMA_MODEL` to use a locally running Ollama instance.
- Set `YUKI_PROVIDER=hybrid` to use local Ollama by default. To route specific task names to cloud, configure `YUKI_CLOUD_MODEL`, `OPENAI_API_KEY` (or the environment-variable name in `YUKI_CLOUD_API_KEY_ENV`), and a comma-separated `YUKI_CLOUD_TASKS` allow-list. Optional endpoint override: `YUKI_CLOUD_BASE_URL`, HTTPS only.
- Provider selection is exposed through `/providers` and `/provider select NAME`. Ordinary free-text input is sent to the currently selected provider through `YukiAgent.ask()`.
- The current terminal runtime does **not** expose `/ask-cloud` or a model-directed `/task GOAL` planner command; do not assume those commands are available based on module presence.
- Durable task execution is explicit and operator initiated: create a goal with `/durable goal create`, submit a registered skill using `/durable task submit KEY | GOAL_ID | SKILL | ARGUMENT`, inspect with `/durable task list GOAL_ID`, cancel with `/durable task cancel TASK_ID`, and execute one task with `/durable run`. For high-risk skills, grant a one-shot approval for the exact argument using `/skill approve NAME ARGUMENT` before running the task.

**Limitations:** the local SQLite queue supports a fixed registered-skill invocation task type and is not a distributed queue. Each durable skill task is configured for one attempt to avoid automatically replaying potentially side-effecting skills after an ambiguous failure. Running tasks cannot be force-cancelled: the worker must reach an outcome or its lease must expire, because clearing a live lease could hide an operation that is still changing external state. Skill code still runs in-process; the durable worker is not a sandbox. Native provider tool-calling, autonomous model-driven task creation, distributed worker coordination, and independent security review are not claimed. The Ollama server/model and cloud credentials must be configured by the user.

## Production phase capability foundations (guarded scaffolds)

The registry includes the following new skills, but **registration is not the same as a fully integrated product feature**:

- `video_generator`: writes a local job manifest; it does not render video until a local generation backend is implemented and configured.
- `auto_personalizer`: stores only explicitly opted-in preference signals locally; no silent conversation profiling.
- `chat_organizer`: indexes user-provided snippets locally; it does not import or scrape account chat history.
- `smart_home_full` and `car_integration`: fail closed without an injected, reviewed adapter. No smart-home devices or vehicle are contacted by default.
- `device_scanner`: reviews a user-supplied device posture inventory; it is not an on-device or network vulnerability scanner.
- `canvas_workspace`: local Markdown documents only; no real-time multi-user collaboration service.
- `financial_manager`: manual local ledger only; no bank sync, payments, investment recommendations, or trading.
- `health_agent`: local wellness notes only; not diagnosis, treatment, or emergency monitoring.
- `home_security`: manually entered event log only; no live camera, sensor, alarm, or monitoring integration.
- `messaging_expanded`: local drafts only; no message delivery.
- `core/tiered_autonomy.py` and `tiered_autonomy`: label the autonomy tier but never bypass the global fresh-approval requirement for state-changing or external actions.

These modules use bounded inputs and local persistence where applicable. Treat sensitive files in the workspace as private. Integrations that can affect devices, external accounts, health, finances, or communications require separate threat modeling, reviewed adapters, explicit approvals, and dedicated integration tests before production use.

## Next production phase: prediction, critique, mentoring, content and engineering

The following modules are registered as bounded foundations, not claims of full external integration:

- `prediction_engine`: aggregates 2–12 forecasts explicitly supplied in the request and reports disagreement. It does not instantiate six independent agents or validate a BlackSwanX-style forecast process.
- `multi_model_critique`: requires two injected model callbacks. It fails closed if they are not configured and makes no model calls by default.
- `ai_mentor`: stores local learning goals and progress; adaptive tutoring/quiz generation still needs a configured model.
- `ai_humanizer`: returns a clarity/editing brief. It does not rewrite text by itself, evade AI detectors, or certify authorship.
- `youtube_shorts` and `long_video_generator`: produce production outlines only; TTS, media generation, FFmpeg rendering, and YouTube upload are not implemented by these wrappers.
- `seo_geo_optimizer`: performs an offline heuristic checklist against supplied content; it does not crawl sites, validate schema, predict rankings, or publish `llms.txt`.
- `conversation_analyzer`: reads bounded JSON exports from inside the configured workspace; no HTML/ZIP/attachment parsing or semantic model analysis is included.
- `vibe_coder`: creates a reviewable engineering plan only. It does not write project files, execute commands, or push code.

The new tests cover registry wiring, input bounds, workspace path containment, and fail-closed provider behavior. CI status must be checked independently; a commit is not evidence that tests passed.


### Explicit task synchronization

The CLI supports `/sync tasks preview` and `/sync tasks queue`. Preview is read-only; queue requires a paired device and places allow-listed task events in the local outbox only. It does not upload data. Network sync remains disabled until conflict-safe remote application is implemented. See [Windows + Oracle Cloud sync documentation](docs/WINDOWS_OCI_SYNC.md).


### Task-only network sync (feature branch)

`/sync run` now performs a bounded HTTPS sync cycle for task upserts only. It requires a paired device. Remote task revisions are validated and applied idempotently; unsupported collections and deletes are rejected without acknowledging the event. Concurrent edits may require manual conflict resolution. See [sync architecture and setup](docs/WINDOWS_OCI_SYNC.md).
