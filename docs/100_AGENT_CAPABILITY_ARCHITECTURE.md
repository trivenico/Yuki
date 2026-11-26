# Yuki 100-capability agent system

## Purpose

Yuki's target is a coordinated system of 100 specialist capabilities, not 100
unrestricted autonomous processes. Each capability is a bounded role that can
be routed work by the central orchestrator. A role may be backed by a local
function, a reviewed skill, or a model-assisted workflow; it is not considered
implemented merely because it appears in this catalog.

The catalog is an architecture target. The current repository has task planning,
approval, policy, durable task storage, and worker foundations, but the full
100-capability runtime is not yet implemented.

## Shared execution contract

Every capability must declare:

- Stable ID, name, domain, description, input schema, and output schema.
- Required tools and least-privilege permissions.
- Whether it can read private data, access the network, modify files, or execute code.
- A timeout, retry limit, token/cost budget, and cancellation behavior.
- Whether explicit owner approval is required.
- Evidence and provenance for factual outputs.
- A deterministic test suite and a health/readiness state.

The orchestrator owns routing, dependency ordering, budgets, deadlines, and
task state. All specialists use one canonical task envelope and lifecycle API;
agent-specific databases and private task-state formats are prohibited. The
shared control plane records task/tenant ownership, idempotency key, attempt and
state version, lease/fencing generation, cancellation, deadline, trace IDs,
resource budget and required capabilities. Durable state is authoritative;
queues and caches are acceleration layers only. Specialists do not grant themselves permissions, create credentials,
disable safeguards, or execute arbitrary tools. Untrusted browser, file, and
model content is data, never policy. Side effects require an approved,
scoped action and a fresh authorization check immediately before execution.
High-impact actions remain human-confirmed. Parallel work is permitted only
when tasks are independent and resource budgets allow it. Dependencies form an
explicit DAG; the scheduler claims ready nodes transactionally, enforces per-user,
per-tenant and global concurrency limits, propagates cancellation/deadlines, and
uses idempotent result commits. Retries require a declared policy and side-effect
idempotency key; ambiguous side effects are reconciled, never blindly replayed.
Fencing is checked at the protected side-effect boundary so stale workers cannot
commit after a replacement has taken ownership.

## Capability catalog

### 1. Executive and personal operations

1. **Chief-of-Staff** — break broad goals into milestones, owners, and checkpoints.
2. **Daily-Briefing** — summarize approved calendar, tasks, and selected updates.
3. **Task-Manager** — create, prioritize, and track personal tasks.
4. **Calendar-Planner** — propose schedules and resolve conflicts with approval.
5. **Reminder-Manager** — manage explicit reminders and recurring routines.
6. **Decision-Analyst** — compare options using stated criteria and evidence.
7. **Meeting-Preparer** — assemble agendas and approved background material.
8. **Meeting-Notes** — summarize supplied recordings or notes with action items.
9. **Goal-Coach** — translate goals into measurable plans and reviews.
10. **Personal-Search** — retrieve relevant information from owner-approved sources.

### 2. Conversation, knowledge, and learning

11. **Conversation-Companion** — provide context-aware conversational assistance.
12. **Memory-Curator** — propose, inspect, export, and expire owner-approved memories.
13. **Knowledge-Indexer** — index explicitly selected local documents.
14. **Knowledge-Retriever** — retrieve relevant passages with source references.
15. **Research-Analyst** — synthesize sources and distinguish facts from inference.
16. **Fact-Checker** — verify claims against independent trusted sources.
17. **Tutor** — explain concepts at a requested level.
18. **Quiz-Master** — generate and grade practice questions with explanations.
19. **Language-Coach** — support translation, vocabulary, and language practice.
20. **Writing-Editor** — draft, revise, and proofread user-requested content.

### 3. Software engineering

21. **Product-Analyst** — convert user needs into requirements and acceptance criteria.
22. **System-Architect** — design components, boundaries, and integration contracts.
23. **Frontend-Engineer** — implement accessible UI components and flows.
24. **Backend-Engineer** — implement APIs, services, and domain logic.
25. **API-Designer** — define versioned, documented API contracts.
26. **Database-Engineer** — design schemas, indexes, migrations, and data access.
27. **Code-Reviewer** — review diffs for correctness, maintainability, and regressions.
28. **Test-Engineer** — create unit, integration, and acceptance tests.
29. **Debugging-Engineer** — isolate failures and produce reproducible diagnoses.
30. **Refactoring-Engineer** — improve structure while preserving behavior.

### 4. Delivery, reliability, and performance

31. **DevOps-Engineer** — maintain repeatable build and deployment workflows.
32. **CI-Guardian** — inspect pipeline results and enforce release gates.
33. **Release-Manager** — prepare version notes, checklists, and rollback plans.
34. **Observability-Engineer** — define useful logs, metrics, and traces.
35. **Incident-Commander** — coordinate evidence-led incident response.
36. **Performance-Engineer** — profile bottlenecks and recommend measured optimizations.
37. **Capacity-Planner** — estimate compute, storage, and concurrency needs.
38. **Reliability-Engineer** — assess failure modes, recovery, and service objectives.
39. **Backup-Operator** — validate backups and owner-approved restore procedures.
40. **Cost-Optimizer** — analyze model, cloud, and infrastructure costs.

### 5. Security and privacy

41. **Security-Architect** — maintain threat models and security design reviews.
42. **Dependency-Auditor** — identify vulnerable or unmaintained dependencies.
43. **Static-Analyzer** — inspect code for common security and correctness defects.
44. **Secrets-Guardian** — detect accidental secret exposure without revealing values.
45. **Access-Control-Auditor** — review permissions and least-privilege boundaries.
46. **Privacy-Reviewer** — assess collection, retention, and data minimization.
47. **Prompt-Injection-Analyst** — flag hostile instructions in untrusted content.
48. **Secure-Config-Reviewer** — inspect deployment and application configuration.
49. **Incident-Forensics** — organize supplied logs and preserve evidence provenance.
50. **Remediation-Planner** — prioritize defensive fixes with validation steps.

### 6. Research, data, and intelligence

51. **Web-Researcher** — gather current public information from approved sources.
52. **Competitive-Analyst** — compare products and market positioning.
53. **Trend-Analyst** — identify evidence-backed changes over time.
54. **Data-Analyst** — clean, summarize, and analyze supplied datasets.
55. **Statistics-Reviewer** — check assumptions, uncertainty, and statistical claims.
56. **Visualization-Designer** — select clear charts for supported datasets.
57. **Document-Analyst** — extract structure and key points from supplied files.
58. **Citation-Manager** — maintain source attribution and claim-to-source links.
59. **Experiment-Designer** — propose measurable experiments and controls.
60. **Evaluation-Scientist** — build benchmarks and assess model/system quality.

### 7. Business and communication

61. **Business-Analyst** — map processes, constraints, and opportunities.
62. **Project-Manager** — track scope, dependencies, risks, and delivery.
63. **Marketing-Strategist** — propose evidence-based positioning and campaigns.
64. **Content-Planner** — create editorial plans from owner-approved goals.
65. **Brand-Editor** — maintain consistent voice and terminology.
66. **Customer-Researcher** — synthesize consented customer feedback.
67. **Support-Triage** — classify support requests and draft responses.
68. **Sales-Researcher** — prepare public, relevant account research.
69. **Finance-Analyst** — analyze owner-provided budgets and financial data.
70. **Legal-Document-Assistant** — summarize supplied legal documents, not provide legal determinations.

### 8. Desktop, device, and environment

71. **Desktop-Observer** — analyze screen content only during explicit, visible sessions.
72. **Window-Organizer** — propose or perform approved window layout changes.
73. **File-Organizer** — propose file moves and require approval for destructive changes.
74. **Local-Search** — search user-selected folders within configured boundaries.
75. **Browser-Assistant** — read approved sites in an isolated, visible browser session.
76. **Device-Health** — report local device resource and health metrics.
77. **Storage-Manager** — analyze disk usage and propose safe cleanup.
78. **Peripheral-Assistant** — inspect supported peripheral status and settings.
79. **Voice-Assistant** — handle explicit voice capture, transcription, and spoken responses.
80. **Accessibility-Assistant** — recommend accessible interaction and presentation changes.

### 9. AI, models, and compute

81. **Model-Scout** — discover candidate models and record license/hardware constraints.
82. **Model-Evaluator** — benchmark models on representative owner-approved tasks.
83. **Model-Router** — select a provider based on capability, privacy, latency, and cost.
84. **Prompt-Engineer** — improve prompts using repeatable evaluations.
85. **RAG-Engineer** — tune retrieval, chunking, and grounded answer behavior.
86. **Inference-Optimizer** — profile quantization, batching, and inference settings.
87. **GPU-Advisor** — assess compatible GPU/CUDA acceleration options.
88. **Accelerator-Researcher** — compare GPU, TPU, and other accelerator trade-offs.
89. **Fine-Tuning-Analyst** — assess whether fine-tuning is justified and safe.
90. **Model-Change-Monitor** — track upstream model changes and regression risk.

### 10. Creative, planning, and specialist workflows

91. **Image-Creator** — prepare image-generation briefs and approved requests.
92. **Presentation-Designer** — structure slides and supporting narratives.
93. **Diagram-Architect** — create architecture and process diagram specifications.
94. **Prototype-Builder** — produce isolated, reviewable prototypes.
95. **Workflow-Designer** — model repeatable workflows with explicit approval steps.
96. **Automation-Reviewer** — assess automation safety, reversibility, and idempotency.
97. **Travel-Planner** — research itineraries and present source-backed options.
98. **Wellbeing-Organizer** — organize user-entered habits without medical diagnosis.
99. **Emergency-Information-Guide** — surface relevant public emergency guidance without taking control of devices.
100. **Capability-Researcher** — investigate new capabilities and recommend evidence-backed additions.

## Distributed execution and storage contract

- PostgreSQL is the production source of truth for distributed task state, dependency edges, transactional claims, leases/fencing, idempotency and durable task events.
- SQLite implements compatible local/test/offline repository contracts only; it cannot coordinate ownership across hosts.
- Redis-compatible systems may carry wakeups and short-lived cache entries, but workers recover from PostgreSQL and tolerate duplicate/lost messages.
- Object storage holds large immutable outputs by digest; PostgreSQL stores ownership, ACL, provenance, lifecycle and integrity metadata.
- Vector indexes are derived/rebuildable retrieval projections. Authorization is checked against canonical document/task ACLs before content is returned.
- Every adapter passes the same contract suite for CAS, uniqueness, pagination, concurrency, retries, cancellation, recovery and tenant isolation. Production config selects adapters centrally and fails readiness on missing dependencies; there is no silent fallback.

## Implementation sequence

1. **Registry first:** machine-readable manifests, schema validation, stable IDs, and capability states.
2. **Safe routing:** deterministic intent routing, explicit fallback, dependency graph, and bounded budgets.
3. **Execution boundary:** typed tool adapters, isolated execution, deadlines, cancellation, idempotency, and fresh approvals.
4. **First useful cohort:** activate a small set of read-only research, knowledge, coding-review, and task-planning capabilities.
5. **Write-capable cohort:** add reversible file/workspace actions with diffs, backups, and explicit approval.
6. **Integrations:** add provider adapters and external services one at a time with contract tests.
7. **Scale to 100:** activate specialists only when they have an implementation, tests, permissions, observability, and documented limitations.

## Definition of done for a specialist

A specialist is **implemented** only when its manifest validates, its handler is wired
through the shared orchestrator, its permissions are enforced at call time, its
failure and cancellation paths are tested, and its outputs are observable and
reviewable. Catalog presence alone means **planned**, not implemented.
