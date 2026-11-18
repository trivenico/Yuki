# Yuki AI Company Operating Model

**Status: governance and architecture specification; not an implemented workforce.**

This document combines the AI-company roles supplied by the project owner with the wider technology, cybersecurity, product, data, commercial, corporate-operations, digital-earning and digital-business role catalogue in the accompanying role list.

## 1. Executive structure

- **Founder / Owner / Board authority — human:** sets company purpose, budget, risk appetite and strategic direction; retains final authority over legal commitments, hiring, firing, compensation, banking, contracts, public statements and high-impact actions.
- **Yuki — AI CEO / Chief Orchestrator:** receives the founder's goals, proposes strategy, delegates bounded work to specialist agents, tracks outcomes, resolves routine coordination conflicts and reports progress. “CEO” is an operating role for the AI system, not a claim that Yuki is a human officer or can legally bind a company.
- **Chief of Staff / Executive Assistant agent:** maintains the operating cadence, decision log, meeting briefs, task queue and cross-team dependencies.
- **Functional leads:** CTO/Engineering, Chief Research Officer, CISO/Security, Chief Product Officer, Chief Data Officer, COO/Operations, CFO/Finance, General Counsel/Legal, CMO/Growth and Chief Commercial/Partnerships.
- **Independent control functions:** Security Assurance, Privacy/Legal Review, Evaluation & Quality, and Finance Controls must be able to block unsafe or unsupported work. They do not report operationally to the agent whose work they review.

The founder is the final approver. Yuki may recommend, prepare and coordinate; it cannot silently create real employees, open bank accounts, sign contracts, spend money, make employment decisions, publish externally, or change security controls.

## 2. Operating principles

1. **Roles are not agents yet.** A title in this catalogue is a role definition. It becomes an executable agent only after it has a tested handler, documented inputs/outputs, capability allow-list, permission policy, resource budget, audit trail and owner.
2. **One CEO, many specialists.** Yuki owns task decomposition and coordination. Specialists return evidence and proposed outputs; they do not spawn peers or expand their own permissions.
3. **Human approval for consequential actions.** Approval is required for external communication, purchases, contracts, legal/HR/financial decisions, production deployments, data deletion, credential changes, security changes, and any action that affects another person.
4. **Least privilege and separation of duties.** Research agents can read approved sources; code agents work in isolated branches; security agents start read-only; finance agents use read-only data until explicit approval. No agent approves its own high-risk work.
5. **Bounded execution.** Each task has a goal, scope, deadline, maximum steps, token/compute budget, allowed tools, data classification and stop conditions. No unrestricted shell, browser session, network, filesystem or model access.
6. **Evidence before claims.** Reports distinguish verified facts, estimates, hypotheses, missing evidence and recommendations. Tests and deployment claims must cite the exact commit/run.
7. **Privacy and provenance.** Use only lawfully obtained, licensed or explicitly authorized data. Preserve source attribution and licenses. Do not use private employee/customer data or voice samples outside their consented purpose.
8. **No fabricated staff.** Agent identities are software roles, not people, employees, contractors or professional license-holders. Human domain experts are required where law, medicine, finance, employment or regulated decisions demand them.
9. **No autonomous self-expansion.** Agents cannot create new privileged agents, change policies, approve their own tools, alter audit logs or disable controls.
10. **Fail closed.** Missing approvals, unclear identity, policy conflicts, tool failures or exhausted budgets stop the task and return it to Yuki/founder.

## 3. Functional departments

| Department | Executive owner | Mission |
|---|---|---|
| Executive Office & Strategy | Yuki, AI CEO | Priorities, operating plan, cross-team coordination, founder reporting |
| Research & Frontier AI | Chief Research Officer | Architecture research, training, post-training, evaluations |
| Engineering & Infrastructure | CTO | Product engineering, platform, cloud, data, inference and reliability |
| Product & Design | CPO | Product discovery, UX, roadmap, delivery quality |
| Data & Human Feedback | Chief Data Officer | Data governance, lawful sourcing, annotation quality, feedback |
| Security, Safety & Privacy | CISO + independent assurance | Defensive security, AI safety, privacy, threat modelling, incident response |
| Commercial & Partnerships | Chief Commercial Officer | Sales engineering, customer success, partnerships, developer relations |
| Growth & Marketing | CMO | Content, SEO, campaigns, brand, community and analytics |
| Operations & People | COO | Workflow, recruiting support, IT, facilities and administration |
| Finance & Legal | CFO + General Counsel | Budget analysis, bookkeeping support, IP, contracts, compliance |

## 4. Specialist agent catalogue (100 planned roles)

These are **100 role slots**, not 100 deployed autonomous agents. Multiple closely related roles may share one implementation initially. Start with a small number of proven domain agents and expand only when evaluation shows a real need.

### Executive office (1–6)
1. `EXEC-01` Chief of Staff — priorities, agendas, decisions, follow-ups.
2. `EXEC-02` Strategy Analyst — market/technology options and strategic briefs.
3. `EXEC-03` Business Operations Analyst — operating metrics and process bottlenecks.
4. `EXEC-04` Program Manager — milestones, dependencies and delivery risks.
5. `EXEC-05` Executive Assistant — schedules, briefings and approved correspondence drafts.
6. `EXEC-06` Knowledge & Decision Archivist — decision records, provenance and retrieval.

### Research and frontier AI (7–18)
7. `RES-01` Research Scientist — architecture and training-method research.
8. `RES-02` Research Engineer — reproducible research implementations.
9. `RES-03` Applied Scientist — translate research into product experiments.
10. `RES-04` Pretraining Engineer — dataset and base-model training plans.
11. `RES-05` Post-training / RLHF Engineer — preference and alignment pipelines.
12. `RES-06` Evals Engineer — benchmark design and regression suites.
13. `RES-07` Quantitative Researcher — statistical modelling and uncertainty.
14. `RES-08` Interpretability Researcher — model-behaviour analysis.
15. `RES-09` Alignment Researcher — instruction-following and control research.
16. `RES-10` Multimodal Researcher — audio, image, video and cross-modal methods.
17. `RES-11` Data-Centric AI Researcher — data quality and curation experiments.
18. `RES-12` Research Librarian — paper review, citations and license notes.

### Engineering and infrastructure (19–34)
19. `ENG-01` Frontend Engineer — desktop/web UI.
20. `ENG-02` Backend Engineer — APIs, services and persistence.
21. `ENG-03` Full-Stack Engineer — end-to-end product slices.
22. `ENG-04` Desktop / Systems Engineer — native desktop lifecycle and portability.
23. `ENG-05` Mobile Engineer — optional secondary mobile client.
24. `ENG-06` Platform Engineer — internal developer platform.
25. `ENG-07` Cloud / Infrastructure Engineer — cloud runtime and infrastructure-as-code.
26. `ENG-08` Site Reliability Engineer — uptime, observability and recovery.
27. `ENG-09` DevOps / Release Engineer — CI/CD, packaging and release provenance.
28. `ENG-10` Database Engineer — schema, migrations, backups and restore tests.
29. `ENG-11` ML Engineer — model training and serving.
30. `ENG-12` Inference Engineer — latency, throughput and cost optimisation.
31. `ENG-13` GPU / Kernel Engineer — CUDA, Triton and accelerator kernels.
32. `ENG-14` Distributed Systems Engineer — queues, coordination and scale-out.
33. `ENG-15` MLOps Engineer — experiment tracking and model lifecycle.
34. `ENG-16` QA / Test Engineer — deterministic tests and release acceptance.

### Data and human feedback (35–42)
35. `DATA-01` Data Engineer — ingestion and transformation pipelines.
36. `DATA-02` Data Strategy Lead — lawful data strategy and source selection.
37. `DATA-03` Dataset Curator — filtering, deduplication and provenance.
38. `DATA-04` Annotation Operations Manager — human-labeling workflow design.
39. `DATA-05` Annotation Quality Lead — inter-rater quality and audits.
40. `DATA-06` Feedback Analyst — aggregate product feedback analysis.
41. `DATA-07` Privacy & Retention Steward — minimisation, retention and deletion workflows.
42. `DATA-08` Domain Expert Coordinator — recruit and coordinate qualified human reviewers.

### Security, safety, privacy and policy (43–56)
43. `SEC-01` CISO / Security Architect — security strategy and threat model.
44. `SEC-02` Application Security Engineer — secure design and code review.
45. `SEC-03` Cloud Security Engineer — cloud posture and identity controls.
46. `SEC-04` IAM Engineer — authentication and least-privilege access.
47. `SEC-05` Network Security Engineer — network boundaries and segmentation.
48. `SEC-06` SOC Analyst — triage approved security telemetry.
49. `SEC-07` Incident Responder — incident coordination and evidence preservation.
50. `SEC-08` Threat Hunter — defensive hypothesis-led investigations.
51. `SEC-09` Detection Engineer — detection logic and false-positive evaluation.
52. `SEC-10` Malware Analyst — isolated defensive malware analysis.
53. `SEC-11` Digital Forensics Analyst — evidence handling and chain of custody.
54. `SEC-12` AI Red Teamer — authorized adversarial testing in scoped environments.
55. `SEC-13` AI Safety / Responsible AI Lead — misuse, fairness and safety review.
56. `SEC-14` GRC / Privacy Auditor — controls, compliance evidence and independent review.

### Product, design and documentation (57–66)
57. `PROD-01` Chief Product Officer — product strategy and prioritisation.
58. `PROD-02` Product Manager — requirements and acceptance criteria.
59. `PROD-03` Technical Program Manager — cross-team delivery.
60. `PROD-04` UX Researcher — user research synthesis.
61. `PROD-05` UX / Interaction Designer — flows and interaction patterns.
62. `PROD-06` UI / Visual Designer — visual system and interface consistency.
63. `PROD-07` AI Conversation Designer — assistant interaction and persona quality.
64. `PROD-08` Accessibility Specialist — accessibility audits and remediation plans.
65. `PROD-09` Technical Writer — API docs, guides and runbooks.
66. `PROD-10` Developer Experience Engineer — SDKs, examples and onboarding.

### Commercial, community and growth (67–78)
67. `GTM-01` Chief Commercial Officer — go-to-market coordination.
68. `GTM-02` Developer Relations Lead — community, demos and technical education.
69. `GTM-03` Enterprise Account Executive — sales preparation and pipeline analysis.
70. `GTM-04` Solutions Engineer — technical discovery and demonstrations.
71. `GTM-05` Solutions Architect — customer integration design.
72. `GTM-06` Customer Success Manager — onboarding and retention insights.
73. `GTM-07` Partnerships Manager — cloud, compute, data and channel partnerships.
74. `GTM-08` Growth Marketer — experiments and acquisition.
75. `GTM-09` SEO / GEO Specialist — search visibility and content structure.
76. `GTM-10` Content Strategist — editorial plans and content briefs.
77. `GTM-11` Brand / Communications Lead — brand consistency and draft communications.
78. `GTM-12` Community Manager — community health and moderation proposals.

### Operations, people, finance and legal (79–90)
79. `OPS-01` Chief Operating Officer — operational processes and service delivery.
80. `OPS-02` People Operations Analyst — workforce planning support, not hiring decisions.
81. `OPS-03` Technical Recruiter Assistant — role descriptions and candidate coordination drafts.
82. `OPS-04` IT Support / SysAdmin — approved device support procedures.
83. `OPS-05` Procurement Analyst — vendor comparison and purchase recommendations.
84. `OPS-06` Finance / FP&A Analyst — forecasts and budget variance analysis.
85. `OPS-07` Accounting Assistant — categorisation and reconciliation support.
86. `OPS-08` Legal Research Assistant — legal issue spotting for qualified counsel.
87. `OPS-09` IP / Copyright Analyst — license and provenance checks for human review.
88. `OPS-10` Compliance Coordinator — obligations register and evidence collection.
89. `OPS-11` Risk Manager — enterprise risk register and mitigation tracking.
90. `OPS-12` Workplace / Executive Administration — facilities and administrative drafts.

### Digital earning and digital business (91–100)
91. `BIZ-01` Digital Business Strategist — business-model research and validation.
92. `BIZ-02` E-commerce Operations Analyst — store catalogue and fulfilment analysis.
93. `BIZ-03` SaaS Product Growth Agent — subscription product experiments.
94. `BIZ-04` Digital Products Producer — templates, courses and downloadable assets.
95. `BIZ-05` Creator Economy Strategist — video, newsletter and podcast plans.
96. `BIZ-06` Affiliate / Partnerships Analyst — disclosure-compliant partner research.
97. `BIZ-07` Agency Operations Manager — service delivery and capacity planning.
98. `BIZ-08` Conversion Rate Optimisation Analyst — funnel measurement and experiments.
99. `BIZ-09` Marketing Analytics Specialist — attribution and KPI reporting.
100. `BIZ-10` Monetisation & Portfolio Analyst — compare revenue models and downside risks.

## 5. Mapping the supplied role list

The supplied AI-company list is represented mainly in Research, Engineering, Data, Safety, Product and GTM departments. The broader technology/cybersecurity/digital-business master list is represented across Engineering, Security, Product, Data, Commercial, Operations and Digital Business above.

Some source roles are intentionally grouped rather than given one agent each: mobile/embedded/hardware, compute/datacenter, penetration testing/exploit development, threat intelligence, cryptography, policy/societal impact, sales/account management, specialist marketing, creator roles and marketplace operators. These may become separate roles when Yuki has a concrete, safe workflow and evaluation for them.

Career levels (IC, management and C-suite) are **human career frameworks**, not agent autonomy levels. Compensation figures in the supplied material are market estimates for human roles; they are not agent costs, salaries or budget assumptions. Human domain experts and licensed professionals remain human roles.

## 6. Agent contract required before implementation

Every executable specialist must declare:
- stable `agent_id`, version, department, role owner and purpose;
- accepted task schema, output schema and evidence requirements;
- explicit read/write/network/tool capability IDs (default deny);
- allowed data classes and workspace/resource boundaries;
- maximum wall time, steps, tokens, retries, concurrency and spend;
- approval class and approver; independent reviewer for consequential outputs;
- audit event schema, trace/correlation ID and redaction policy;
- cancellation, timeout, error and rollback behaviour;
- unit, integration, security, adversarial and evaluation tests;
- model/provider fallback policy with no hidden remote fallback.

The orchestrator should dispatch **capabilities**, not arbitrary natural-language instructions to a named agent. The registry must reject duplicate IDs, unknown capabilities, invalid budgets, missing owners and unreviewed handlers. Agent output is untrusted input until validated.

## 7. Decision and approval matrix

| Action | Agent may prepare | Agent may execute without fresh approval |
|---|---:|---:|
| Research public sources / summarise approved workspace files | Yes | Yes, within allow-list and budget |
| Draft code or documentation in isolated branch | Yes | Yes, non-production branch only |
| Run tests / static analysis in sandbox | Yes | Yes, within resource limits |
| Read approved, connected business data | Yes | Only under explicit connector scopes |
| Send email, publish content, contact customers | Yes | No |
| Buy services, spend funds, sign or accept contracts | Yes | No |
| Hire, reject, discipline or terminate a person | No final decision | No |
| Legal, medical, tax or regulated financial advice | Research support only | No professional determination |
| Deploy to production / modify firewall / change IAM | Prepare plan | No; human approval and change controls |
| Delete user data, rotate keys, change retention | Prepare plan | No; explicit confirmation and recovery plan |
| Create agents, grant permissions, alter policies | Propose specification | No |

## 8. Existing implementation audit (main branch, 2026-11-18)

The repository already contains substantial pieces of Phase A. The implementation plan must extend these components rather than build a second agent framework.

| Existing component | Verified responsibility | Remaining gap for this operating model |
|---|---|---|
| `automation/agent_coordinator.py` | SQLite-backed specialist registry; stable IDs; capability and permission routing; enabled/disabled state; bounded dependency plans; idempotency; authorization checks; dispatch through TaskRuntime | Add reviewed role metadata and owner/version/handler readiness; define role-to-capability mappings; improve manifest lifecycle and acceptance coverage |
| `automation/task_runtime.py` + `automation/workflow_store.py` | Durable task records, authenticated authorization callbacks, approval/session binding, versioned transitions, event journal, cancellation and task summaries | Ensure budgets and task envelopes are consistently enforced across every worker path; document one canonical task runtime |
| `automation/workflow_engine.py` | Small declarative action allow-list, bounded step/argument/output sizes, explicit approval callback | Specialist action handlers remain deliberately narrow; add no action until a concrete handler and security tests exist |
| `yuki_agent_runtime.py` + `yuki_agent_worker.py` | Separate SQLite metadata queue and explicit handler map with leases, cancellation checks and budgets | This is a second runtime surface; do not make it the default company orchestrator or duplicate automation runtime policy without a migration decision |
| `skills/registry.py` + `skills/catalog.py` | Existing skill discovery/catalog and guarded invocation path | Define how a specialist requests an approved skill capability without receiving unrestricted access to the entire registry |
| `tests/test_agent_coordinator.py`, task-runtime and skill-catalog suites | Existing authorization, routing, idempotency, dependency, cancellation, worker and skill invocation coverage | Add organization-role contract tests and cross-layer tests proving agent permissions cannot exceed the registered manifest |

**Important implementation distinction:** a registered specialist in the coordinator is currently a routing manifest, not an autonomous employee or a self-contained model. The coordinator does not itself invoke an LLM. The workflow engine's supported specialist action is deliberately limited to `emit`; task execution still depends on the reviewed runtime/worker integration. A role must not be reported as active merely because it appears in the 100-role catalogue or can be inserted into SQLite.

**Current maturity:** the durable coordinator, task lifecycle, approval and skill-catalog foundations exist in code. The full 100-role workforce, CEO-level autonomous delegation, role-specific model prompts, independent review workflow, production-grade cross-agent budget accounting and integrated desktop company dashboard are not established by this audit.

## 8. Phased implementation

**Phase A — close the existing foundation gaps (first):** define a versioned role manifest that extends SpecialistAgent rather than replacing it; bind role owner, department, handler readiness, input/output schema, data classification, resource budgets and approval class to the existing coordinator; prove the task runtime is the canonical execution journal; and add cross-layer tests for manifest-to-capability enforcement, cancellation, budget exhaustion and approval revalidation. Keep unimplemented role slots disabled and non-dispatchable. Do not add a second registry, queue or permission system.

**Phase B — first useful team:** Chief of Staff, Research Analyst, Software Engineer, QA/Evals, Security Reviewer and Technical Writer. These roles support the desktop core and can be evaluated without financial or external side effects.

**Phase C — product and operations:** Product Manager, SRE/DevOps, Data Engineer, UX, documentation and controlled business analytics.

**Phase D — model lab:** research, training, post-training, inference, GPU and data-operations agents after compute budgets, dataset licensing and evaluation infrastructure exist.

**Phase E — commercial and digital business:** sales, marketing, partnerships, creator/e-commerce and monetisation workflows; external actions remain approval-gated.

**Phase F — scale-out:** expand toward all 100 roles only when role-specific evaluation demonstrates quality, safety, reliability and a real workload. Parallelism is bounded; more agents must not mean more uncontrolled tool access.

## 10. Initial success criteria

- One canonical registry and one orchestrator; no duplicated agent/skill permission systems.
- Every dispatch is traceable to a user goal, policy decision and bounded task.
- A specialist cannot call tools outside its declared capabilities.
- Prompt injection in research results cannot change permissions or system policy.
- Cancellation and budget exhaustion stop all child work.
- No high-impact action occurs without explicit human approval.
- Reports identify unimplemented roles honestly; a catalogue entry never counts as an implemented agent.
- The desktop assistant remains the primary product path; the organisation framework must not delay completing the core desktop experience.
