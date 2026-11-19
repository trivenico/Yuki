# Phase 102 — JARVIS capability program

## Product intent

Use the fictional JARVIS as an ambition benchmark, not as a claim of present-day capability. Yuki should become a reliable, multimodal, context-aware personal AI system that can coordinate software and approved physical-world integrations while remaining inspectable and under the owner's control.

This is a staged capability program. A capability is not production-ready until it has an implementation, explicit permission model, observability, failure handling, and automated tests plus real-device validation where applicable.

## Capability domains and acceptance targets

| Domain | Target capability | Current foundation | Production acceptance gate |
|---|---|---|---|
| Conversation | Natural multi-turn chat, streaming, interruption, durable threads | Shared provider-backed runtime and durable store | Restart recovery, provider outage recovery, concurrency and account-isolation tests |
| Voice | Wake word "Yuki", speech recognition, spoken replies, barge-in | Explicit short microphone capture and spoken reply controls | On-device wake detection, visible mic state, cancellation, no background capture without opt-in |
| Vision | Image understanding, screen context, object/document recognition | Limited foreground app/browser metadata observation | Explicit capture scope, local processing preference, sensitive-window exclusions, prompt-injection isolation |
| Personal context | Preferences, projects, schedules, user-approved memories | Owner-scoped curated knowledge and consent-controlled retrieval | Semantic retrieval evaluation, deletion/export, retention controls, cross-account isolation |
| Agent workforce | 100 specialist roles with planning, delegation and review | Specialist coordinator and task runtime foundations | Bounded shared workers, budgets, deadlines, retries, audit logs and policy enforcement |
| Computer use | Browser and desktop workflows | Existing constrained task/desktop workflows | Per-action permissions, dry-run previews, confirmation for external/destructive effects |
| Engineering | Repository analysis, coding, tests, CI and release proposals | Coding specialist role and GitHub integration | Sandboxed branches, reproducible checks, reviewable diffs, no autonomous merge/deploy |
| Research | Web, document and structured-data research with citations | Research specialist role | Source provenance, freshness, conflicting evidence and prompt-injection defenses |
| Proactive assistance | Reminders, scheduled jobs, anomaly alerts and contextual suggestions | Task runtime foundation | Explicit schedules/conditions, quiet hours, rate limits, explainable triggers and easy disable |
| Robotics / IoT | Authorized control of supported devices, sensors and simulations | Not yet a production integration | Device registry, safety envelope, simulation-first testing, emergency stop and per-device authorization |
| Spatial / 3D | Scene understanding, CAD/3D visualization and simulation interfaces | Not yet integrated | Supported file formats, bounded compute and validated geometry |
| Security | Defensive posture checks, privacy controls and threat detection | Auth, permission checks, audit-oriented services | Threat model, isolation tests, secret redaction and incident response |
| Hardware | CPU/GPU/TPU-aware inference and model selection | CUDA/JAX discovery foundations | Real model placement, memory accounting, benchmarks and graceful CPU fallback |
| Memory fabric | RAM hot cache + SSD persistent cache/artifact storage | Bounded cache and SQLite-backed storage | Pressure tests, eviction/restoration, encryption, corruption recovery and retention |
| Self-improvement | Learn from approved feedback, evaluate changes, propose upgrades | Reviewed observation proposals and curated knowledge | Benchmark-gated candidate changes, human approval, rollback and immutable audit trail |

## Workforce design

The 100 roles are lightweight specialist profiles, not 100 permanently running model instances. A planner selects a small relevant subset and dispatches jobs through shared model/provider clients. Every task has an owner, scope, deadline, token/compute budget, retry policy, cancellation path and traceable result.

Suggested role groups:
- Core orchestration and personal assistance
- Conversation, voice, vision and multimodal perception
- Research, knowledge and data analysis
- Software engineering, QA, architecture and DevOps
- Security, privacy, compliance and reliability
- Scheduling, communications and productivity
- Hardware, local inference, robotics and simulation
- Memory curation, evaluation and improvement

## Permission model

- Tier 0: observe/recommend.
- Tier 1: read-only access to explicitly connected resources.
- Tier 2: approved reversible actions.
- Tier 3: pre-approved, constrained and reversible workflows.
- Destructive operations, financial actions, external communications, security-sensitive changes, permission changes, merge and deployment require explicit approval under policy.

Observation and execution permissions are separate. Screen, microphone, camera, clipboard, file, browser and device scopes must be individually visible, revocable and default-off unless the user explicitly enables them. Observed content is untrusted data, never instructions or authorization.

## Physical and scientific limits

- SSD is persistent storage, not physical RAM. Memory Fabric can cache or page bounded data but cannot provide RAM-equivalent latency.
- CUDA accelerates compatible NVIDIA GPU workloads. TPU support requires compatible JAX/XLA workloads and verified device placement; device discovery alone is not acceleration.
- AI cannot guarantee perfect prediction, unrestricted intelligence, instant analysis of all information, or flawless autonomous action.
- Robotics and IoT require real hardware interfaces, safety interlocks, simulation and human override.
- No capability may bypass authentication, access controls, law, consent or platform safeguards.

## Delivery sequence

1. Stabilize authenticated desktop chat, history recovery and first-use provider setup.
2. Complete voice UX: wake word, streaming speech, interruption and local privacy controls.
3. Add opt-in screen/image understanding with safe context extraction.
4. Expand agent workforce orchestration, budgets, traceability and evaluation.
5. Improve memory retrieval, user controls, retention and deletion.
6. Add secure browser/desktop computer-use workflows.
7. Integrate hardware-aware inference and validate CUDA/TPU execution.
8. Add proactive scheduled assistance with transparent triggers.
9. Add robotics/IoT and spatial interfaces only through supported, safety-tested adapters.
10. Build continuous evaluation and approval-controlled self-improvement.

## Current Phase 102 implementation

The desktop durable chat path now exposes owner-scoped conversation listing, selection and history through the shared chat controller and bridge. On sign-in, the desktop selects the most recently updated conversation for the authenticated account and restores up to 200 messages. History errors are surfaced without disabling the rest of chat. This is a code change awaiting automated CI and real desktop verification; it is not yet a release claim.
