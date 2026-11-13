# Yuki

Yuki is a modular personal-agent project combining a companion interface, guarded skills, local memory, developer workflows, optional voice, and defensive security-lab foundations.

**Readiness status: not production-ready yet.** A bounded JSON-based model-guided task loop is now present and requires fresh approval for every registered-skill invocation; it is not a production-grade native tool-calling/ReAct runtime. There is no autonomous project builder, always-on wake-word service, tested custom wake-word model, production firewall auto-expiry/recovery, or independent security review. This repository deliberately reports those gaps rather than implying that module files alone make a finished assistant.



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

To run the authenticated browser chat MVP, install the optional ASGI server and start the local launcher:

```sh
python -m pip install -r requirements-web.txt
# First launch only: provide a unique admin username and strong password.
export YUKI_BOOTSTRAP_ADMIN_USERNAME='your-admin'
export YUKI_BOOTSTRAP_ADMIN_PASSWORD='use-a-long-unique-password'
python scripts/run_web.py
```

Open `http://127.0.0.1:8000`. Configure a real provider (for example `OPENAI_API_KEY`) or a supported local provider first. The web launcher exposes no tools by default. Public binding is disabled unless explicitly enabled and must only be used behind a trusted TLS reverse proxy. See [Phase 159 web chat deployment notes](docs/PHASE159_AUTHENTICATED_WEB_CHAT.md). Administrators can switch the server-wide active provider from the chat header when an implemented provider is configured and ready; Yuki persists the provider ID under the workspace and requires explicit confirmation for global and cloud routing changes. See [Phase 161 provider switching](docs/PHASE161_PROVIDER_SWITCHING.md).

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


## Installable CLI (first release slice)

Python 3.10+ is required. From a source checkout, install the declared package and run the stable console command:

```sh
python -m pip install .
yuki doctor
yuki provider-status
yuki ask "Hello, Yuki"
yuki chat
```

The installed CLI creates a user-owned configuration from packaged safe defaults on first use. See [installation and provider setup](docs/INSTALLATION.md). This packaging milestone provides the stable CLI entry point; it does not make the broader agent runtime, desktop application, or product production-ready.


## Yuki's own model ecosystem

**Yuki is intended to have its own model family and model-serving layer.** It will not be only a front end that forwards every request to a third-party API. The model strategy is to start from capable, already-trained open-weight models and build Yuki-specific models and services on top of them—not to pretrain a foundation model from random initialization as the default path.

The intended model ecosystem includes:

- **Yuki Core:** the primary conversational and reasoning model, adapted for Yuki's interaction style, instruction following, tool-use conventions, and policy-aware workflows.
- **Yuki Code:** a coding-focused model or adapted coding checkpoint for repository understanding, implementation, debugging, testing, and review.
- **Yuki Vision:** a vision-language model for supported image and document understanding.
- **Yuki Speech:** speech recognition, speech generation, and optional voice components selected and evaluated as a coherent voice stack.
- **Yuki Embeddings and Reranker:** retrieval models for semantic memory, project knowledge, document search, and evidence ranking.
- **Yuki Specialist adapters:** task-specific adapters or routed models for research, analysis, security, operations, writing, and other specialist domains. These remain governed by the same agent/tool permissions; model specialization never grants authority.

### How Yuki models will be built

1. Select strong open-weight base checkpoints that fit the target quality, hardware, latency, and deployment needs. Verify the actual model licence, commercial-use permissions, redistribution terms, acceptable-use rules, and provenance individually.
2. Use suitable openly licensed datasets and/or properly licensed synthetic examples for supervised fine-tuning, instruction tuning, domain adaptation, and evaluation. Dataset availability does not itself grant permission to train on it; record source, licence, consent/provenance, filtering, and intended use.
3. Apply parameter-efficient methods such as LoRA/QLoRA where appropriate before considering expensive full fine-tuning. Use distillation only when the teacher model and generated training data permit it.
4. Build Yuki-specific tool-use, agent-routing, structured-output, multilingual, safety, and workflow datasets from permitted sources. Keep held-out evaluation sets separate from training data.
5. Evaluate quality, factuality, tool correctness, prompt-injection resistance, privacy leakage, multilingual performance, latency, memory/GPU use, and regression before promoting a checkpoint.
6. Version model weights, adapters, tokenizer/configuration, datasets, evaluation reports, licences, and deployment manifests. Support rollback and reproducible model releases.
7. Serve local models through a provider-neutral inference layer, with optional cloud models as explicitly configured fallbacks or specialist choices. The owner can choose local-only, cloud-enabled, or hybrid routing.

**Important distinctions:** using an open-weight model is not the same as owning its original foundation weights; fine-tuning creates a Yuki-adapted checkpoint/adapter subject to the base model's licence. Retrieval-augmented generation, prompts, tools, and orchestration improve Yuki but do not by themselves create a new trained model. Yuki-specific model releases are a target capability, not a claim that custom-trained Yuki weights already exist in this repository.

User conversations, private files, credentials, and personal memory must never be silently added to training data. Any contribution to model improvement requires separate, informed opt-in, minimization/redaction, retention controls, and a documented deletion policy. Model downloads and training artifacts belong in user-controlled data/model storage, not Git.

Current state: **Partial/Planned.** Yuki has provider adapters and a local Ollama path, but the current repository documentation does not establish a released Yuki-owned fine-tuned model family, curated training corpus, reproducible fine-tuning pipeline, or model-evaluation/release process.

## Yuki: complete product blueprint and delivery checklist

**Product direction:** build Yuki as a complete, owner-controlled personal AI operating environment—not merely a chat wrapper, a collection of skill stubs, or a demo. The full target architecture, capability catalogue, acceptance criteria, safety rules, and implementation-status definitions are maintained in [Yuki Product Blueprint](docs/YUKI_PRODUCT_BLUEPRINT.md). This blueprint is the source of truth for product scope; update it whenever a capability is added, changed, deferred, or removed.

### What the complete Yuki is intended to do

- **Understand and converse:** natural multilingual conversations, context-aware reasoning, planning, clarification, summaries, structured outputs, and multimodal understanding where configured.
- **Think and orchestrate:** route work across configured local/cloud models, plan and critique work, use specialist agents, coordinate parallel tasks, maintain durable state, recover safely, and explain decisions.
- **Operate as a 100-specialist-agent system:** a governed catalogue of focused agents across engineering, research, security, operations, productivity, learning, content, business, personal assistance, and integrations. Agents are bounded specialists—not 100 uncontrolled autonomous processes—and share one identity, policy, memory, audit, and approval framework.
- **Remember with user control:** searchable long-term and working memory, project knowledge, preferences, retrieval, explicit correction/deletion, retention controls, and transparent provenance.
- **Get work done:** durable workflows, schedules, reminders, event triggers, resumable jobs, browser/API integrations, document handling, workspace operations, and user-approved external actions.
- **Be a software-engineering partner:** inspect repositories, understand architecture, plan changes, edit code, run permitted tests, debug failures, review security/performance, prepare pull requests, and report exact evidence. Destructive or externally consequential actions remain approval-gated.
- **Research and create:** web/document research, source-grounded reports, knowledge extraction, writing, presentations, image/media workflows, and content production through configured backends.
- **Support everyday life and work:** personal organization, study/coaching, accessibility, health-information organization (not diagnosis), manual finance organization (not unauthorized banking/trading), and compliant business/income-support workflows.
- **Speak and listen when enabled:** explicit voice sessions, speech-to-text, text-to-speech, interruption, language/voice preferences, and optional wake-word operation with visible controls. Voice assistant functionality is in scope; **phone calling/telephony is not**.
- **Connect to the owner's environment:** reviewed LAN, local-device, smart-home, vehicle, and external-service adapters with pairing, least privilege, allow-lists, explicit permissions, and audit trails. No silent microphone/camera activation or unrestricted device control.
- **Offer optional companion experiences:** user-selected roles and an opt-in adult companion mode with transparent AI identity and healthy boundaries. No manipulation, coercion, possessiveness, dependency engineering, or exclusivity claims.
- **Run reliably:** secure identity/session isolation, encrypted secret handling, backups, observability, policy enforcement, deployment tooling, migrations, disaster recovery, performance testing, and documented operations.

### Implementation truth

The complete target is **not yet delivered**. The current repository contains useful foundations and partial workflows, but some features remain scaffolds, require external adapters, or are not wired end to end. In particular, do not infer a finished capability from a registry entry, module, roadmap phase, or passing isolated test.

- Current README-documented foundations include provider adapters/routing, a bounded agent/task loop, local memory and knowledge foundations, approval-gated skills, a durable local task queue, optional voice primitives, a small authenticated web-chat runtime, and defensive/security-lab components with explicit limitations.
- Owner-isolated provider profiles and durable approval/recovery/reconciliation work is being developed in [PR #787](https://github.com/trivenico/Yuki/pull/787). It is **open, draft, and unmerged**; it is not yet a released mainline capability.
- The complete 100-agent catalogue, robust autonomous orchestration, production-grade long-term memory, end-to-end voice conversation, desktop-grade product UX, broad verified integrations, compliant income workflows, and production deployment/operations are target capabilities—not claims of completion.
- The README and blueprint must use the status labels **Implemented**, **Partial**, **Scaffold**, **Planned**, **Blocked**, and **Out of scope**. Only call a capability Implemented when its real runtime path, security controls, tests, and documented setup are present.

### Product-wide non-negotiable requirements

1. **No artificial Yuki usage quotas:** do not impose arbitrary daily/monthly/lifetime message, token, task, or agent-invocation caps. Clearly disclose actual provider billing/rate limits, hardware capacity, configured concurrency, and safety backpressure; never claim third-party limits can be removed.
2. **One security model:** every agent, skill, tool, provider, connector, and interface uses the same authenticated owner/session context, policy checks, approvals, audit, and isolation boundaries.
3. **No unsafe autonomy:** no arbitrary shell/code execution, hidden persistence, credential harvesting, unapproved purchases/messages/publications, unauthorized scraping, or automatic replay of uncertain side effects.
4. **Privacy by default:** explicit consent for sensitive data and sensors; minimize collection; protect secrets; provide export, retention, and deletion controls.
5. **Honest status:** distinguish code present, runtime integrated, tested, externally configured, production-ready, and released. No fabricated test, CI, deployment, or capability claims.
6. **Income and platform compliance:** assist with legitimate research, planning, drafting, analysis, and permitted repetitive work. Respect platform terms, labor rules, intellectual property, and anti-abuse controls; no spam, fake engagement/accounts/reviews, deception, or unauthorized automation.
7. **No telephony/calling feature:** voice conversation and speech interfaces are allowed; phone calls and calling integrations are deliberately excluded.

See the blueprint for the detailed capability map, proposed specialist-agent domains, architecture, security model, phased delivery gates, and acceptance checklist.



### Hybrid deployment and governed model improvement

The target operating model is **Oracle Cloud Linux for production**, **Kaggle for isolated GPU training and evaluation**, **Hugging Face for pretrained model/dataset discovery and approved artifact distribution**, and **GitHub for source control, CI, review, and release automation**. This is a planned integration architecture, not a claim that these services are already provisioned or connected.

Yuki's improvement loop will be governed: collect only permitted/minimized signals -> propose a code or model improvement -> version it in GitHub -> run tests and evaluations -> require authorized review -> stage and monitor on OCI -> promote or roll back. Kaggle workspaces must not receive production secrets or private user data. Model and dataset revisions, licences, provenance, training configurations, evaluation reports, and artifact hashes must be recorded. Private conversations, memory, files, and credentials are excluded from training by default; no silent online training, self-permission changes, or autonomous production deployment.

See [the hybrid deployment and self-improvement blueprint](docs/YUKI_PRODUCT_BLUEPRINT.md#11-hybrid-cloud-training-and-governed-self-improvement-architecture) for responsibilities, artifact lifecycle, routing, security boundaries, and acceptance gates.


### Portable desktop and supervised computer use

Yuki's target includes a portable desktop client for external SSD/USB storage, with a bundled per-platform Python runtime, secure authenticated connection to the Oracle Cloud Yuki service, optional local inference, and per-device revocation. An external SSD is recommended for performance and endurance; a USB flash drive can carry the client but may be unsuitable for heavy model/database workloads. “No installation” means no full system installation for supported user-space scenarios—not bypassing OS security, endpoint protection, code-signing, or accessibility permissions. Windows, macOS, and Linux require separately packaged and tested builds.

Yuki may observe a selected screen/window and control approved desktop interactions only in an explicitly started, visibly indicated, revocable session. A local permission-enforcing broker, scoped actions, redaction, audit, pause/stop controls, and confirmation for consequential actions are mandatory. No silent capture, arbitrary shell access, credential-store access, security-control bypass, or hidden persistence is allowed. Screen contents are private data and are not used for training by default.

See [the portable desktop and computer-use blueprint](docs/YUKI_PRODUCT_BLUEPRINT.md#12-portable-desktop-client-and-supervised-computer-use-architecture). This capability is **Planned**, not yet delivered.
