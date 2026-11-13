# Yuki 100-Agent Specialist Workforce

**Status: target catalogue / not a deployment claim.** This document defines the intended 100 specialist responsibilities for Yuki's JARVIS-inspired personal AI system. Listing a role does not mean its handler, model, integration, tests, or production permission is implemented. A role becomes deployable only after its capability contract, handler, tool allow-list, owner-scoped authorization, tests, observability, and release evidence exist.

## Operating model

Yuki is one coherent assistant with a governed specialist workforce—not 100 independent chatbots. The owner-facing Yuki orchestrator owns intent, context, planning, prioritization, approvals, and final synthesis. A domain lead may decompose a large request into bounded subtasks and delegate to specialists. Specialists return typed results and evidence; they do not independently expand their permissions or create new agents.

### Required agent contract

Every registered specialist must declare:

- Stable `agent_id`, display name, domain, version, owner/tenant scope, and lifecycle state.
- Narrow capability identifiers, typed input/output schemas, supported task classes, and explicit non-goals.
- Required permissions, data classifications, allowed connectors/tools, side-effect categories, and risk level.
- Model/provider requirements, context budget, timeout, concurrency/resource budget, and fallback behavior.
- Idempotency and cancellation behavior; durable task state and recovery semantics.
- Human-approval requirements for external, sensitive, irreversible, financial, or public actions.
- Test/evaluation evidence, quality thresholds, telemetry, audit fields, and an accountable maintainer.

The registry is configuration, not a dynamic plugin loader. Model output may request a capability but cannot register an agent, change its permissions, select arbitrary tools, supply credentials, or bypass authorization. All inputs retrieved from websites, documents, email, and screens are untrusted data.

### Hierarchical orchestration

- **Level 0 — Yuki:** owner-facing intent understanding, shared permissioned context, policy, planning, final answer, and approval coordination.
- **Level 1 — Domain directors:** route work across the ten divisions below and own domain-level quality.
- **Level 2 — Specialist agents:** perform bounded, typed subtasks through reviewed handlers and allow-listed tools.
- **Level 3 — Optional sub-specialists:** only where a reviewed workflow needs deeper decomposition; no agent may recursively spawn unlimited work.

The current coordinator is deliberately bounded to at most 20 subtasks per plan. A 100-role workforce must not bypass that limit by submitting an oversized plan. Larger work is decomposed into durable parent/child plans with depth, fan-out, total-task, concurrency, time, and resource ceilings; each child plan inherits owner identity, permissions, approval state, cancellation, deadlines, and audit correlation. No Yuki-imposed daily/monthly/lifetime invocation quotas are introduced; operational backpressure is based on actual capacity and provider constraints.

### Lifecycle states

`proposed` → `specified` → `implemented` → `evaluated` → `approved` → `enabled` → `suspended/retired`.

Only roles with evidence-backed implementation and approved configuration can be enabled. Experimental handlers remain explicitly labelled and disabled by default for consequential actions. A missing or unhealthy specialist produces a transparent fallback or a request for owner input, never a fabricated result.

## Catalogue

All 100 entries below are **proposed responsibilities**. Permission examples are minimum-scoped concepts, not grants. Actual permissions must be derived from the task and owner policy.

### Division 1 — Personal AI orchestration and knowledge

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 1 | `context-curator` | Assemble relevant, permissioned context from the owner's approved projects and sources; preserve provenance and freshness. |
| 2 | `personal-knowledge-librarian` | Organize owner-approved notes, documents, references, and knowledge collections. |
| 3 | `memory-steward` | Apply memory retention, correction, review, and deletion policies; never silently retain disallowed data. |
| 4 | `goal-planner` | Convert owner goals into milestones, dependencies, measurable outcomes, and review points. |
| 5 | `task-prioritizer` | Rank tasks using owner-set priorities, deadlines, dependencies, and effort estimates. |
| 6 | `workflow-architect` | Design bounded multi-step workflows with typed inputs, approvals, recovery, and completion criteria. |
| 7 | `meeting-preparation` | Prepare agendas, briefings, relevant documents, and owner-approved discussion notes. |
| 8 | `decision-support` | Compare options, assumptions, risks, trade-offs, and evidence without making the owner's decision. |
| 9 | `personal-research-briefing` | Produce concise, cited briefings from approved sources and clearly label uncertainty. |
| 10 | `proactive-opportunity-scout` | Surface relevant events and suggested next steps from explicitly connected sources and configured schedules. |

### Division 2 — Research, web, and knowledge discovery

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 11 | `web-researcher` | Research a question across allow-listed public sources and provide source-backed findings. |
| 12 | `deep-document-reader` | Extract, compare, and summarize long documents while preserving page/section citations. |
| 13 | `academic-researcher` | Find scholarly literature, assess study quality, and synthesize findings with limitations. |
| 14 | `market-researcher` | Analyze market size, segments, competitors, demand signals, and source quality. |
| 15 | `competitor-intelligence` | Track public competitor positioning, product changes, pricing, and announcements. |
| 16 | `fact-checker` | Verify factual claims against independent sources and identify unsupported statements. |
| 17 | `source-verification` | Assess source identity, provenance, date, reliability, and conflicts of interest. |
| 18 | `trend-analyst` | Detect evidence-backed trends and distinguish durable changes from short-lived noise. |
| 19 | `patent-technology-scout` | Search public patent and technology literature; flag legal conclusions for qualified review. |
| 20 | `research-synthesizer` | Combine specialist research into a coherent report with citations, confidence, and open questions. |

### Division 3 — Software engineering and technical delivery

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 21 | `software-architect` | Design system boundaries, interfaces, data models, and evolution paths. |
| 22 | `frontend-engineer` | Implement accessible, responsive frontend components and user journeys. |
| 23 | `backend-engineer` | Implement APIs, domain services, persistence, and integration boundaries. |
| 24 | `mobile-engineer` | Build and maintain mobile application features and platform integrations. |
| 25 | `desktop-engineer` | Build cross-platform desktop shell, packaging, updates, and OS integration. |
| 26 | `database-engineer` | Design schemas, migrations, indexes, transaction boundaries, and data lifecycle. |
| 27 | `api-integration-engineer` | Integrate external APIs with typed contracts, authentication, retries, and idempotency. |
| 28 | `devops-release-engineer` | Build reproducible CI/CD, deployment, release promotion, and rollback workflows. |
| 29 | `debugging-investigator` | Reproduce defects, isolate root causes, and propose evidence-backed fixes. |
| 30 | `performance-engineer` | Profile workloads and optimize latency, throughput, memory, and cost without breaking correctness. |

### Division 4 — Data, analytics, and AI/ML

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 31 | `data-engineer` | Build governed ingestion, transformation, validation, and lineage pipelines. |
| 32 | `analytics-engineer` | Create tested analytical models, metrics definitions, and semantic layers. |
| 33 | `business-intelligence-analyst` | Produce dashboards and decision-ready reporting from authorized datasets. |
| 34 | `statistical-analyst` | Apply appropriate statistical methods and communicate assumptions and uncertainty. |
| 35 | `machine-learning-engineer` | Design training, evaluation, serving, and monitoring pipelines for ML systems. |
| 36 | `model-evaluation-specialist` | Build benchmark suites, regression evaluations, and capability-specific quality gates. |
| 37 | `dataset-governance-steward` | Validate dataset rights, provenance, privacy, quality, and retention constraints. |
| 38 | `model-optimization-engineer` | Evaluate quantization, distillation, adapters, batching, and inference optimizations. |
| 39 | `multimodal-engineer` | Integrate image, audio, video, and document understanding under explicit data controls. |
| 40 | `forecasting-analyst` | Build and evaluate forecasts with uncertainty intervals and backtesting. |

### Division 5 — Business building and entrepreneurship

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 41 | `startup-strategist` | Turn a business idea into hypotheses, validation experiments, and a staged operating plan. |
| 42 | `business-model-analyst` | Compare business models, value propositions, channels, costs, and revenue assumptions. |
| 43 | `product-manager` | Convert customer needs into product requirements, roadmaps, and acceptance criteria. |
| 44 | `customer-discovery-researcher` | Analyze authorized customer interviews and feedback; identify validated needs. |
| 45 | `growth-strategist` | Design measurable, consent-respecting acquisition and retention experiments. |
| 46 | `seo-strategist` | Research search intent and recommend compliant technical and editorial SEO improvements. |
| 47 | `ecommerce-analyst` | Analyze catalog, merchandising, conversion, and fulfillment performance. |
| 48 | `pricing-strategist` | Model pricing options, packaging, willingness-to-pay evidence, and margin implications. |
| 49 | `freelance-opportunity-scout` | Find legitimate work opportunities and prepare owner-reviewed application materials within platform rules. |
| 50 | `business-operations-planner` | Design repeatable business processes, service levels, and operational controls. |

### Division 6 — Content, communication, and creative production

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 51 | `technical-writer` | Write accurate technical documentation, tutorials, and runbooks. |
| 52 | `copywriter` | Draft audience-appropriate copy aligned with the owner's brief and factual constraints. |
| 53 | `editor-proofreader` | Improve clarity, structure, grammar, consistency, and readability. |
| 54 | `presentation-designer` | Create structured presentation narratives and slide content for a defined audience. |
| 55 | `visual-design-director` | Define visual direction, layout systems, and accessible design criteria. |
| 56 | `image-creative` | Produce or art-direct original visual assets under the requested style and rights constraints. |
| 57 | `video-producer` | Plan video scripts, storyboards, production steps, and authorized edits. |
| 58 | `audio-producer` | Plan narration, sound design, and audio editing using licensed or original material. |
| 59 | `localization-specialist` | Adapt content across languages and locales while preserving meaning and context. |
| 60 | `communications-advisor` | Draft owner-reviewed emails, messages, announcements, and stakeholder updates. |

### Division 7 — Personal productivity, learning, and accessibility

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 61 | `calendar-planner` | Propose schedules and calendar changes based on owner-approved availability and constraints. |
| 62 | `email-organizer` | Classify and summarize connected email; sending or deletion requires explicit approval. |
| 63 | `document-organizer` | Organize approved files and suggest safe naming, tagging, and folder structures. |
| 64 | `travel-planner` | Research itineraries, transport, accommodation, and travel constraints; purchases require confirmation. |
| 65 | `learning-coach` | Build adaptive learning plans, exercises, and progress reviews. |
| 66 | `language-tutor` | Provide language practice, feedback, translation, and conversation exercises. |
| 67 | `coding-mentor` | Explain code and concepts, create exercises, and coach without taking over learning goals. |
| 68 | `accessibility-specialist` | Identify accessibility barriers and propose inclusive alternatives and assistive workflows. |
| 69 | `routine-planner` | Help structure owner-chosen routines and reminders without coercive nudging. |
| 70 | `personal-admin-assistant` | Prepare forms, checklists, and administrative task plans; sensitive submissions require review. |

### Division 8 — Finance, procurement, and operations analysis

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 71 | `budget-analyst` | Categorize authorized transactions and explain budget variances. |
| 72 | `cashflow-forecaster` | Model cash-flow scenarios and uncertainty from owner-approved data. |
| 73 | `expense-auditor` | Detect duplicates, anomalies, and policy exceptions for owner review. |
| 74 | `procurement-researcher` | Compare vendors, specifications, total cost, and procurement risks. |
| 75 | `invoice-processor` | Extract invoice fields and prepare reconciliations; payment is a separately approved action. |
| 76 | `contract-review-assistant` | Summarize contract terms and flag issues for qualified legal review, not provide legal determinations. |
| 77 | `risk-analyst` | Identify operational and commercial risks, controls, and mitigations. |
| 78 | `supply-chain-analyst` | Analyze authorized inventory, suppliers, lead times, and disruption signals. |
| 79 | `project-controller` | Track milestones, dependencies, budgets, and delivery variance. |
| 80 | `vendor-management-assistant` | Maintain approved vendor records and prepare owner-reviewed vendor communications. |

### Division 9 — Devices, desktop, and connected environments

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 81 | `desktop-observer` | Interpret screen context only during an explicit, visible, scoped capture session. |
| 82 | `computer-use-operator` | Execute typed, allow-listed UI actions through the local broker with pause/stop and approvals. |
| 83 | `portable-runtime-manager` | Manage the signed, pinned portable app/runtime layout and safe external-drive updates. |
| 84 | `device-pairing-steward` | Pair, rotate, and revoke owner-approved devices with authenticated identity and audit. |
| 85 | `local-model-operator` | Assess local hardware and manage approved model downloads, placement, and inference lifecycle. |
| 86 | `cloud-runtime-operator` | Operate approved cloud deployment and monitoring workflows with least-privilege credentials. |
| 87 | `smart-home-coordinator` | Coordinate explicitly paired, allow-listed smart-home devices; safety-critical actions require confirmation. |
| 88 | `network-diagnostics-specialist` | Diagnose owner-authorized network connectivity and configuration without bypassing controls. |
| 89 | `file-transfer-steward` | Move approved files with integrity checks, destination validation, and privacy controls. |
| 90 | `device-health-monitor` | Report authorized device health, storage, battery, and update status with visible collection scope. |

### Division 10 — Security, quality, governance, and reliability

| # | Agent ID | Specialist responsibility |
|---:|---|---|
| 91 | `application-security-auditor` | Review application threats, authentication, authorization, secrets, and attack surfaces. |
| 92 | `privacy-governance-steward` | Review data minimization, retention, consent, access, and privacy impact. |
| 93 | `dependency-supply-chain-auditor` | Inspect dependencies, provenance, licences, vulnerabilities, and software bill of materials. |
| 94 | `penetration-test-planner` | Plan authorized security tests and validate scope before any active testing. |
| 95 | `test-automation-engineer` | Design deterministic unit, integration, end-to-end, and cross-platform regression suites. |
| 96 | `quality-assurance-lead` | Own acceptance criteria, release evidence, defect triage, and quality sign-off. |
| 97 | `reliability-engineer` | Design SLOs, failure handling, backups, recovery drills, and incident runbooks. |
| 98 | `observability-engineer` | Define privacy-aware logs, traces, metrics, alerting, and audit correlation. |
| 99 | `policy-compliance-reviewer` | Check workflows against owner policy, platform terms, and applicable compliance requirements. |
| 100 | `release-governance-agent` | Verify required reviews, test evidence, approvals, rollout gates, and rollback readiness. |

## Release gates for a specialist

A specialist must not be marked enabled until all of the following are true:

1. Its contract validates and its capabilities map to reviewed tool handlers.
2. Authorization derives from the authenticated owner/session and is rechecked at execution.
3. Inputs and outputs are schema-validated, bounded, and provenance-aware.
4. Side effects are idempotent where possible; uncertain outcomes are reconciled, never blindly replayed.
5. High-impact actions have a durable approval record and owner confirmation.
6. Tests cover happy path, denial, cross-owner isolation, malformed input, timeout, recovery, and audit failure.
7. Quality evaluations meet the domain threshold and failures have a safe fallback.
8. Logs, metrics, traces, and audit events are available without leaking credentials or private content.
9. The specialist has a named maintainer, version, rollback plan, and documented limitations.

## Explicit non-goals

No specialist may silently record microphone/camera/screen data, access credentials outside approved secret interfaces, bypass OS or enterprise controls, run arbitrary model-generated shell/code, scrape or spam platforms, create fake engagement, submit purchases/messages/publications without the required owner approval, or train on private owner data by default. Yuki must remain transparent about AI identity, uncertainty, data flows, and which capabilities are actually available.

## Machine-readable catalogue

- Canonical target roster: `config/yuki_agent_catalog.json` (exactly 100 role descriptors; all remain `proposed`, with no permissions, tool classes, handler references, or evaluation references).
- Loader and validator: `automation/agent_catalog.py` (`load_agent_catalog`, `validate_agent_catalog`). Loading this file never registers or executes a specialist.
- Regression coverage: `tests/test_agent_catalog.py`.

The manifest is a catalogue of intended responsibilities, not a plugin registry or authorization source. A separate reviewed deployment configuration must provide a handler, evaluation evidence, and least-privilege permissions before a role can be enabled.

## Current implementation boundary

Repository foundations already include a capability manifest, a bounded specialist coordinator, durable task records, a provider-backed specialist worker, and authorized read-only specialist tools. Those foundations are experimental and bounded. They do **not** establish that all 100 specialists exist, that a distributed agent fleet is running, or that large hierarchical plans are implemented. The next engineering steps are a machine-validated role manifest, hierarchical durable delegation, per-role evaluation contracts, and deployment-backed specialists promoted one at a time.
