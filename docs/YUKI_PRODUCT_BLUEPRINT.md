# Yuki Product Blueprint

> **Status:** Product target and engineering acceptance contract. This document describes the intended complete product, not a claim that every listed feature is implemented. Status must be updated against code, tests, runtime integration, security review, and release evidence.

## 1. Product definition

Yuki is an owner-controlled personal AI operating environment: a conversational assistant, orchestrated specialist-agent workforce, durable workflow engine, personal knowledge system, software-engineering partner, and secure integration layer. It should help one person or an authorized team understand information and complete useful work across local and cloud environments.

Yuki is not simply a chatbot, a prompt collection, a bag of disconnected skills, or an unrestricted autonomous shell. Its capabilities must be composed through a coherent application runtime with consistent identity, policy, memory, observability, approvals, and recovery.

### Product principles

- Full-featured destination: do not silently redefine the target as a limited beta or demo.
- Modular and provider-neutral: local and cloud models can be configured and switched without rewriting product logic.
- Owner-controlled: the owner decides what Yuki can remember, connect to, execute, and share.
- Safe autonomy: autonomy is graduated by risk; low-risk work can proceed within policy, consequential actions require the appropriate approval.
- Evidence-based: every capability has acceptance criteria and an honest status.
- Extensible: new specialist agents and integrations use stable contracts rather than bespoke bypasses.
- No artificial usage caps imposed by Yuki. Provider billing/rate limits, hardware, configured concurrency, and protective backpressure remain real constraints and must be visible.

## 2. Status vocabulary

Use these labels consistently in this document, README, release notes, and project tracking:

| Status | Meaning |
|---|---|
| **Implemented** | Real end-to-end runtime path exists, is integrated, has meaningful tests and documentation, and meets its stated security requirements. |
| **Partial** | A working subset exists, but important runtime paths, UX, integrations, or acceptance criteria remain. |
| **Scaffold** | Interfaces, registry entries, schemas, prototypes, or fail-closed placeholders exist; the user-facing capability is not complete. |
| **Planned** | In the target scope but not yet implemented. |
| **Blocked** | Work is waiting on a dependency, decision, external provider, hardware, or unresolved security issue. |
| **Out of scope** | Deliberately not part of Yuki. |

A module existing does not make a product feature Implemented. A test passing does not prove production readiness. A PR is not released until merged and deployed through the documented release process.

## 3. Capability map

The following is the complete target capability catalogue. Initial status assessments reflect the README's current documented claims and known PR state; they are conservative and must be refined as each subsystem is audited.

### A. Conversational intelligence and multimodality

| Capability | Target behavior | Initial status |
|---|---|---|
| Natural conversation | Multi-turn chat, follow-up context, clarification, summaries, structured answers | Partial |
| Reasoning and planning | Break goals into verifiable steps, identify assumptions, track constraints, critique plans | Partial |
| Multilingual interaction | Detect/respond in the user's language; configurable tone and terminology | Partial |
| Model/provider routing | Local/cloud providers, task-aware routing, fallback and explicit provider selection | Partial |
| Multimodal input | Understand supported images, documents, audio, and other configured media | Planned |
| Structured tool calling | Typed tool schemas, validation, bounded arguments, result handling | Partial |
| Grounded answers | Cite retrieved files/web sources and distinguish evidence from inference | Partial |
| Conversation continuity | Persist and restore authorized conversation state across sessions/devices | Partial |

### B. Specialist-agent workforce: 100 governed roles

Yuki's goal is a catalogue of 100 specialist agents. These are role definitions and bounded workers under a shared orchestrator—not 100 always-running autonomous bots. Each specialist needs a contract, allowed tools, input/output schema, risk class, memory scope, timeout/resource budget, tests, and escalation policy. Agents cannot grant themselves permissions.

| # | Specialist domain | Example responsibilities |
|---:|---|---|
| 01 | Orchestrator | Decompose goals, delegate, sequence, aggregate results |
| 02 | Planner | Build dependency-aware execution plans |
| 03 | Critic | Challenge assumptions and identify omissions |
| 04 | Verifier | Check outputs against evidence and acceptance criteria |
| 05 | Research | Gather and synthesize source-grounded information |
| 06 | Fact-check | Verify claims, dates, and source reliability |
| 07 | Knowledge curator | Organize notes, entities, and project knowledge |
| 08 | Memory curator | Manage memory proposals, provenance, retention |
| 09 | Conversation analyst | Analyze user-provided conversation exports |
| 10 | Personal assistant | Organize requests, priorities, and follow-ups |
| 11 | Calendar planner | Plan schedules and resolve conflicts |
| 12 | Reminder manager | Create and track reminders |
| 13 | Task manager | Maintain tasks, dependencies, and status |
| 14 | Workflow designer | Compose reusable workflows |
| 15 | Workflow operator | Run authorized workflow steps |
| 16 | Browser researcher | Read approved websites and extract information |
| 17 | Document analyst | Parse, compare, and summarize documents |
| 18 | File organizer | Classify and organize approved workspace files |
| 19 | Writer | Draft documents and communications |
| 20 | Editor | Improve clarity, tone, and structure |
| 21 | Translator | Translate while preserving meaning and format |
| 22 | Meeting assistant | Prepare agendas and summarize supplied notes |
| 23 | Presentation creator | Build slide narratives and presentations |
| 24 | Spreadsheet analyst | Analyze tables and create formulas/reports |
| 25 | Data analyst | Clean, transform, and explain datasets |
| 26 | Visualization analyst | Select and explain appropriate charts |
| 27 | Software architect | Design modular systems and interfaces |
| 28 | Backend engineer | Implement APIs and domain services |
| 29 | Frontend engineer | Build accessible application interfaces |
| 30 | Full-stack engineer | Deliver integrated vertical features |
| 31 | Mobile engineer | Build supported mobile clients |
| 32 | Desktop engineer | Build desktop clients and packaging |
| 33 | API engineer | Design/version/document APIs |
| 34 | Database engineer | Schema, migrations, indexing, query design |
| 35 | Performance engineer | Profile latency, throughput, memory, cost |
| 36 | Debugging engineer | Reproduce, isolate, and fix defects |
| 37 | Test engineer | Unit, integration, acceptance, regression tests |
| 38 | QA analyst | Validate workflows and usability |
| 39 | Code reviewer | Review correctness, maintainability, regressions |
| 40 | Refactoring engineer | Reduce complexity while preserving behavior |
| 41 | Dependency auditor | Review versions, licenses, vulnerabilities |
| 42 | Security architect | Threat models and security architecture |
| 43 | AppSec engineer | Input validation, auth, secrets, injection defenses |
| 44 | Privacy engineer | Data minimization, consent, retention, deletion |
| 45 | Identity engineer | Authentication, sessions, authorization |
| 46 | Access-policy agent | Least privilege and permission evaluation |
| 47 | Approval steward | Human approval lifecycle and recovery |
| 48 | Audit analyst | Integrity, event trails, incident evidence |
| 49 | Threat intelligence analyst | Defensive threat research and advisories |
| 50 | Defensive network analyst | Authorized network posture and alert triage |
| 51 | Incident responder | Containment plans and recovery coordination |
| 52 | Secure configuration agent | Harden configuration and deployment defaults |
| 53 | DevOps engineer | Build/release automation and environments |
| 54 | SRE | Reliability objectives, runbooks, incident review |
| 55 | CI/CD engineer | Pipelines, artifacts, test gates |
| 56 | Infrastructure architect | Capacity, topology, scaling, resilience |
| 57 | Cloud operations agent | Manage explicitly authorized cloud resources |
| 58 | Local systems agent | Inspect approved local system information |
| 59 | Backup/recovery agent | Backup validation and restore planning |
| 60 | Observability agent | Metrics, logs, traces, actionable alerts |
| 61 | Provider specialist | Configure and diagnose model providers |
| 62 | Model evaluator | Compare model quality, latency, and cost |
| 63 | Prompt engineer | Maintain versioned, tested task instructions |
| 64 | Retrieval engineer | Improve indexing, retrieval, and grounding |
| 65 | RAG evaluator | Measure retrieval relevance and answer faithfulness |
| 66 | Agent-evaluation engineer | Test delegation, tool choice, and task completion |
| 67 | Learning coach | Explain topics and adapt learning plans |
| 68 | Tutor | Generate lessons, examples, and practice |
| 69 | Quiz coach | Create and evaluate practice quizzes |
| 70 | Interview coach | Practice interviews and provide feedback |
| 71 | Career planner | Skills, portfolio, and job-search planning |
| 72 | Research mentor | Teach research methods and source evaluation |
| 73 | Accessibility assistant | Adapt content and workflows to accessibility needs |
| 74 | Wellness organizer | Organize user-entered wellness information, not diagnose |
| 75 | Habit coach | Track opt-in goals and user-defined habits |
| 76 | Personal finance organizer | Manual budgets and ledgers, no unauthorized financial actions |
| 77 | Business analyst | Analyze user-provided business information |
| 78 | Market researcher | Ethical market and competitor research |
| 79 | Product manager | Requirements, prioritization, acceptance criteria |
| 80 | Project manager | Milestones, risks, dependencies, status |
| 81 | Startup strategist | Business-model and launch planning |
| 82 | Income opportunity researcher | Find and assess legitimate opportunities |
| 83 | Freelance assistant | Proposals, scoping, estimates, client drafts |
| 84 | E-commerce analyst | Product/catalog analysis within permitted data access |
| 85 | SEO/GEO analyst | Content quality and discoverability recommendations |
| 86 | Content strategist | Editorial plans and audience research |
| 87 | Social content assistant | Draft compliant posts; no fake engagement |
| 88 | Video producer | Scripts, storyboards, production plans |
| 89 | Image creative assistant | Generate/edit images through configured providers |
| 90 | Audio producer | Speech/audio workflows through configured tools |
| 91 | Smart-home coordinator | Operate paired, allow-listed devices with consent |
| 92 | LAN/device manager | Discover and manage explicitly authorized devices |
| 93 | Vehicle integration assistant | Use reviewed, permissioned vehicle adapters |
| 94 | Email assistant | Search, draft, and send only with explicit authorization |
| 95 | Messaging assistant | Draft and deliver messages only through approved connectors |
| 96 | Travel planner | Research and organize travel; booking requires confirmation |
| 97 | Shopping researcher | Compare products and prepare purchase recommendations |
| 98 | Data annotation assistant | Assist only within dataset/platform rules and permitted workflows |
| 99 | Companion persona | Optional transparent companion interaction with healthy boundaries |
| 100 | Integration developer | Build, test, and maintain reviewed connectors/adapters |

**Agent platform acceptance criteria**

- Stable agent IDs, versioned manifests, schemas, capability declarations, and compatibility checks.
- Shared orchestrator with bounded delegation, parallelism, cancellation, deadlines, and backpressure.
- Per-agent tool allow-lists and policy checks; no privilege inheritance from user-supplied prompts.
- Shared owner/session isolation, scoped memory, trace/correlation IDs, and audit events.
- Durable task state, resumable work, idempotency where safe, and explicit recovery for uncertain side effects.
- Agent evaluation suite: task success, factuality, tool correctness, safety, latency, resource use, and regression tests.
- User-visible progress, delegated-agent explanations, errors, and approval requests.
- No arbitrary agent-count or invocation quota imposed by Yuki; actual compute/concurrency controls must be transparent.

### C. Memory and personal knowledge

Target:
- Short-term conversation context and durable long-term memory with explicit provenance.
- User-approved facts, preferences, projects, goals, and working context.
- Search across notes, files, conversations the user supplies, and connected sources the user authorizes.
- Hybrid lexical/semantic retrieval, ranking, deduplication, source citations, and freshness metadata.
- Memory review, correction, export, retention, deletion, and per-project/per-agent access scopes.
- Knowledge graph and project workspaces with import/export.
- Optional reflection/evolution proposals that require review; no silent self-modification.

Initial status: **Partial**. Local lexical memory, knowledge indexing/graph, and a review-proposal evolution foundation are documented. Production semantic retrieval, comprehensive cross-source memory, and full lifecycle UX remain to be completed.

### D. Task execution and autonomous workflows

Target:
- Goal decomposition, dependency graphs, plans, milestones, and task ownership.
- Durable queue, workers, schedules, triggers, webhooks, retries, deadlines, cancellation, and recovery.
- Human-in-the-loop approvals based on action risk, scope, identity, and policy.
- Idempotency keys, execution receipts, uncertain-outcome reconciliation, and no unsafe replay.
- Workflow templates, visual inspection, run history, and audit exports.
- Local and distributed worker options with resource isolation and observability.

Initial status: **Partial**. A bounded local SQLite task queue and workflow runner are documented. Distributed orchestration, broad trigger/scheduler support, production worker isolation, and complete end-to-end workflow UX remain target work. Owner-isolated approval/provider work in PR #787 is open and unmerged.

### E. Software engineering and repository operations

Target:
- Connect authorized Git repositories and inspect code, tests, dependency manifests, history, and architecture.
- Produce architecture maps, implementation plans, issue breakdowns, and risk assessments.
- Edit files through reviewable changes; run allow-listed commands in isolated workspaces.
- Execute targeted/full test suites, linters, type checks, security scans, and performance benchmarks.
- Diagnose CI failures from logs; propose and validate fixes.
- Review diffs for security, correctness, accessibility, performance, and maintainability.
- Prepare commits and pull requests; merge/deploy only with explicit authorization and configured policy.
- Maintain project-specific engineering memory and progress ledgers.

Initial status: **Scaffold/Partial**. Engineering workflow and specialist worker foundations exist in the repository/CI, but the complete autonomous engineering experience and production isolation must be validated end to end.

### F. Research, content, and knowledge work

Target:
- Web and connected-source research with source quality assessment and citations.
- File/document ingestion for supported formats, OCR where configured, comparison, extraction, and summaries.
- Reports, proposals, emails, specifications, presentations, spreadsheets, and diagrams.
- Image/audio/video generation through configured providers and local tools.
- Content calendars, SEO/GEO quality analysis, localization, and editorial review.
- Provenance, licensing, attribution, and human approval before publication.

Initial status: **Partial/Scaffold**. Browser reading, YouTube caption retrieval, image-generation wrapper, document/knowledge foundations, and content-outline modules are documented; full pipelines and publishing integrations are not complete.

### G. Voice assistant and accessibility

Target:
- Explicit voice sessions with streaming speech recognition and speech synthesis.
- Natural turn-taking, interruption/barge-in, echo handling, endpoint detection, and low-latency responses.
- Configurable language, voice, speed, and accessibility settings.
- Optional local wake-word detection with a visible hardware/microphone indicator and a physical/software off switch.
- Local processing where possible; clear disclosure and consent for remote audio/vision processing.
- Captions, transcripts, keyboard access, screen-reader semantics, and accessible controls.

Initial status: **Partial**. STT/TTS and wake-word adapters are documented, but end-to-end conversational voice, robust barge-in, production speaker verification, and a tested wake-word model are not claimed. **Phone calling/telephony is Out of scope.**

### H. Interfaces and clients

Target:
- Unified application/service layer powering CLI, web, desktop, and mobile interfaces.
- Responsive chat, workspaces, agent/task supervision, approval inbox, memory controls, integrations, settings, and audit views.
- Streaming responses, file upload/download, rich results, and accessible interaction.
- Secure multi-device synchronization and session management.
- Consistent behavior and permission enforcement across clients.

Initial status: **Partial**. CLI and a small authenticated web chat path are documented; a complete polished desktop/mobile product and cross-device UX are not claimed.

### I. Integrations and device environment

Target:
- Versioned provider and connector SDKs with health checks and capability discovery.
- Explicit account linking, OAuth/token vault, scopes, revocation, and audit.
- MCP and API integrations with allow-listed servers/tools and untrusted-result handling.
- LAN pairing and device identity; per-device allow-lists and owner approval for sensitive actions.
- Smart-home, vehicle, storage, calendar, email, messaging, and productivity adapters.
- Sandboxed integration execution and connector contract tests.

Initial status: **Scaffold/Partial**. MCP and several optional integrations exist as limited primitives; many smart-home/car/device integrations are fail-closed adapter placeholders. No default access to devices or external accounts is implied.

### J. Security, privacy, and trust

Required baseline:
- Authentication, session rotation/revocation, owner and tenant isolation, role-based permissions.
- Least-privilege capabilities, explicit scopes, approval gates, and deny-by-default behavior.
- Secret storage in environment/OS credential vault; no secrets in source, prompts, logs, or audit metadata.
- Input validation, output encoding, SSRF protections, prompt-injection defense in depth, safe file paths, and bounded resource use.
- Sandboxing for untrusted code and tools; no unrestricted shell or plugin execution.
- Append-only tamper-evident audit trail, integrity verification, incident exports, and retention controls.
- Encryption in transit and at rest where applicable; backups and tested restoration.
- Threat modeling, dependency/SBOM/license checks, vulnerability management, penetration testing, and independent security review.
- Explicit consent for sensitive data, camera/microphone, location, and external sharing.

Initial status: **Partial**. Approval, audit, auth, guarded runtime, and defensive foundations exist, but production security review and full platform hardening are not complete. Heuristic prompt-injection scanning is not a security boundary by itself.

### K. Personal companion and roles

Target:
- User-selectable roles such as assistant, tutor, developer, researcher, coach, and companion.
- Optional adult companion mode, explicitly enabled and easy to disable.
- Transparent AI identity; user control over tone, memory, and boundaries.
- No claims of being human, emotional coercion, possessiveness, isolation, dependency engineering, or exclusivity.
- Role/persona settings never grant permissions or bypass safety policy.

Initial status: **Partial**. Role/profile foundations are documented; a polished companion product experience remains target work.

### L. Productivity, business, and legitimate income support

Target:
- Research and compare legitimate work/opportunity options.
- Help plan skills, portfolios, proposals, pricing, project scope, and time management.
- Assist with permitted data analysis, annotation preparation, quality checks, and repetitive work only where platform rules permit.
- Support business research, customer discovery, product planning, content drafting, and compliant marketing.
- Keep financial records user-entered unless an explicitly authorized financial connector is integrated.
- Never promise earnings or imply that an opportunity is verified without evidence.

Prohibited: spam, fake accounts, fake reviews/engagement, impersonation, deceptive submissions, unauthorized scraping, evading platform controls, or automating work contrary to platform rules.

Initial status: **Planned/Scaffold**. Some manual finance, content, and business-related foundations are documented; verified end-to-end income-support workflows are not.

### M. Deployment, reliability, and operations

Target:
- Reproducible installation, configuration validation, upgrade/migration and rollback.
- Portable local deployment and optional external SSD data portability.
- Containerized and server deployment profiles with TLS, secret management, and least privilege.
- Metrics, structured logs, traces, health/readiness checks, and alerting.
- SLOs for availability, latency, task completion, and recovery.
- Load, soak, fault-injection, backup/restore, disaster-recovery, and security tests.
- Release channels, signed artifacts, SBOM, provenance, dependency pinning, and rollback.
- Clear support/diagnostic bundles with secrets redacted.

Initial status: **Partial**. Packaging, portable paths, launchers, and CI foundations exist; production deployment, observability, SLOs, and disaster-recovery validation remain.

## 4. Shared reference architecture

All interfaces and agents should use one coherent path:

1. **Interfaces:** CLI, web, desktop, mobile, voice.
2. **Application/API layer:** authentication, sessions, request validation, streaming, rate/backpressure visibility.
3. **Orchestration:** conversation runtime, planner, specialist-agent coordinator, workflow engine, task queue.
4. **Policy plane:** identity, permissions, risk classification, approvals, consent, audit, quotas/limits disclosure.
5. **Capability plane:** typed tools, reviewed skills, connectors, MCP adapters, device adapters.
6. **Intelligence plane:** model/provider router, local/cloud inference, retrieval, memory, evaluators.
7. **Data plane:** conversations, task state, memory, project files, audit, secrets references, backups.
8. **Operations plane:** metrics, logs, traces, health, CI/CD, deployment, recovery.

No interface or agent may bypass the policy plane. Model output is untrusted input, not authorization.


### N. Yuki-owned model ecosystem and training strategy

**Product requirement:** Yuki must develop and ship its own model family/model artifacts over time. The product must not remain solely a wrapper around third-party hosted APIs. This does not mean training a foundation model from scratch. The default strategy is to select capable, already-trained open-weight base models and adapt them into Yuki-specific checkpoints, adapters, and task models.

Target model portfolio:
- **Yuki Core:** conversational reasoning, instruction following, multilingual interaction, planning, and structured responses.
- **Yuki Code:** repository comprehension, code generation, debugging, tests, and review.
- **Yuki Vision:** supported image/document understanding through a suitable vision-language base.
- **Yuki Speech:** a tested speech-to-text and text-to-speech stack; components may use different specialized models.
- **Yuki Embeddings/Reranker:** semantic retrieval, memory search, project knowledge ranking, and evidence selection.
- **Yuki Specialist adapters:** narrowly adapted models for selected agent domains where evaluations justify specialization. Agent permissions remain external policy decisions, never model decisions.

**Build strategy**
1. Select suitable open-weight checkpoints based on capability, size, inference hardware, latency, context length, language coverage, and deployment constraints.
2. Review each checkpoint's exact licence and terms: open weights are not automatically OSI-open-source, commercially usable, redistributable, or unrestricted. Track model card, source, version, licence, acceptable-use policy, and provenance.
3. Build a curated, versioned dataset programme using datasets whose licences and provenance permit the intended use, plus consented or appropriately licensed synthetic examples. Document source, licence, consent, filtering, quality, personal-data handling, and retention.
4. Start with supervised fine-tuning and parameter-efficient methods (LoRA/QLoRA) when appropriate. Consider full fine-tuning only when evidence justifies the cost. Use teacher-model distillation only where the teacher licence and generated-data terms allow it.
5. Train for Yuki-specific behavior: tool schemas, structured outputs, agent handoffs, multilingual response quality, domain workflows, safe refusals, prompt-injection robustness, and policy-aware operation.
6. Maintain separate train/validation/held-out test sets; prevent evaluation leakage and benchmark contamination. Evaluate against strong base-model baselines.
7. Track factuality, task success, tool-call validity, safety, privacy leakage, memorization, bias, multilingual quality, latency, throughput, and hardware cost. Require human review for high-impact model changes.
8. Version and sign model artifacts, adapters, tokenizer/configuration, dataset manifests, training code/config, evaluation reports, licence records, and deployment manifests. Support staged rollout, monitoring, rollback, and reproducibility.
9. Expose model routing as local-only, cloud-enabled, or hybrid. Cloud providers are optional configured capabilities, not a mandatory dependency for every interaction.

**Privacy and governance:** private conversations, memory, files, credentials, and connected-account data are excluded from training by default. Any user-data contribution requires separate informed opt-in, purpose limitation, minimization/redaction, retention/deletion controls, and documented governance. No hidden continual training on user activity.

**Important distinctions:** using an open-weight base model is not the same as owning its original foundation weights. A fine-tuned Yuki checkpoint/adapter remains subject to the base model licence. RAG, prompts, tools, and orchestration are valuable but do not constitute model training. Do not describe Yuki as having its own trained weights until model artifacts and their evaluation/release evidence exist.

Initial status: **Partial/Planned**. Existing local Ollama and cloud provider adapters provide inference foundations. The repository does not yet establish a released Yuki-specific fine-tuned model family, governed training datasets, reproducible fine-tuning pipeline, model registry, or model evaluation/release gates.

## 5. Autonomy and approval model

- **Tier 0 — Explain:** read context supplied to the model; no external side effects.
- **Tier 1 — Read:** access explicitly allow-listed sources and files under scope.
- **Tier 2 — Prepare:** draft changes, messages, commands, or plans without applying them.
- **Tier 3 — Approved action:** perform a specific user-confirmed action with bounded parameters.
- **Tier 4 — Policy-bounded automation:** execute pre-authorized, reversible, low-risk workflows with limits, audit, monitoring, and an immediate disable path.

Higher autonomy is not permission to ignore identity, scope, consent, or audit. Purchases, external messages/publications, destructive changes, device controls, firewall changes, and other consequential operations require explicit confirmation according to risk policy. Uncertain external outcomes must be reconciled from trusted evidence; never replay automatically.

## 6. Usage, capacity, and cost policy

Yuki must not add arbitrary daily, monthly, lifetime, message, token, task, or agent-invocation quotas. It must:
- Surface provider-specific rate limits, billing, and errors honestly.
- Let owners configure concurrency, model routing, local/cloud preference, and budgets/alerts.
- Offer resource backpressure for CPU, memory, disk, GPU, queue depth, and provider limits.
- Make controls configurable and explain when work is delayed or rejected.
- Never claim unlimited provider usage or bypass provider billing/terms.

## 7. Explicit exclusions and safety boundaries

- Phone calling/telephony: **Out of scope**. Voice assistant and speech interaction remain in scope.
- Silent microphone/camera activation or covert recording: prohibited.
- Unrestricted shell, arbitrary code execution, or unsandboxed untrusted plugins: prohibited.
- Unauthorized access, hacking back, credential theft, evasion, or destructive security actions: prohibited.
- Fake engagement, spam, fraudulent income schemes, deceptive reviews, or platform abuse: prohibited.
- Medical diagnosis/treatment, autonomous financial trading, and emergency monitoring are not implied by wellness/finance organization features.
- No hidden self-modification, permission escalation, or automatic merge/deploy without explicit authorization.

## 8. Delivery gates

A capability is not complete until all applicable gates pass:

1. Product requirement and user journey documented.
2. Threat model, data classification, and permission scopes reviewed.
3. Stable interface/schema and migration/backward-compatibility plan added.
4. Runtime integration through the shared application and policy layers.
5. Unit, integration, security, failure-injection, and acceptance tests added.
6. Observability, audit, error handling, recovery, and resource limits added.
7. Setup/configuration and user-facing documentation written.
8. CI passes on supported operating systems and dependency manifests are clean.
9. External provider/hardware requirements are tested or explicitly marked unverified.
10. Release/deployment evidence exists before status changes to Implemented/Released.

## 9. Immediate engineering priorities

1. Complete and verify the owner-isolated provider/approval runtime in PR #787; keep it unmerged until explicitly authorized and all required checks pass.
2. Close approval lifecycle gaps: audit evidence binding, trusted provider receipt verification, concurrency races, terminal-state handling, and crash/recovery tests.
3. Publish an audited capability inventory mapping each registered skill/agent to a real runtime path, permission, test, and status.
4. Establish the versioned specialist-agent manifest and coordinator contract before scaling the catalogue toward 100 roles.
5. Unify provider routing, conversation runtime, memory, task orchestration, and policy enforcement behind the shared application layer.
6. Establish the Yuki model programme: base-model selection/licence audit, dataset governance, fine-tuning/evaluation pipeline, model registry, and local inference releases.
7. Build the complete memory lifecycle and project knowledge retrieval.
8. Deliver end-to-end voice conversation and accessible client experiences.
9. Implement real integrations one at a time with consent, least privilege, contract tests, and revocation.
10. Build production observability, deployment, backup/restore, load and security validation.
11. Track every capability against the tables above; do not mark scaffolds as completed.

## 10. Project tracking rule

Every implementation PR should state:
- Blueprint section and capability IDs affected.
- Previous and resulting status.
- Runtime entry points and interfaces changed.
- Security/permission impact.
- Tests run and CI links.
- External configuration/hardware requirements.
- Known limitations and next acceptance gate.

Keep this document and the README aligned. If product scope changes, update the blueprint explicitly rather than letting requirements disappear from chat history.
