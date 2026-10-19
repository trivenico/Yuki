# Comparative Analysis: Yuki vs. Hermes, OpenClaw, OpenJarvis, and DeepSeek Harness

This document compares Yuki's architecture with prominent open-source AI agent frameworks.

## Framework Comparison Matrix

| Architectural Dimension | Yuki | Hermes Agent | OpenClaw | OpenJarvis | DeepSeek Harness |
|---|---|---|---|---|---|
| **Primary Paradigm** | Secure-by-construction local personal agent | Procedural skills & persistent CLI worker | Cross-device assistant with channel gateways | Local deep research & evaluation presetter | Benchmark & model evaluation harness |
| **Tool Execution Model** | **Fail-closed human approval for every skill call** | Autonomous tool calls with permission profiles | Gateways with scoped permissions | Scheduled & autonomous research loops | Automated benchmark evaluation harness |
| **Model Output Trust** | Untrusted data; no shell execution | Tool-calling model output | Remote & device node tool calls | Evaluated research output | Structured benchmark evaluation |
| **Local Privacy** | Loopback-HTTP / HTTPS only; no implicit cloud fallback | Model/provider abstraction | Device nodes & messaging channels | Local AI & selectable agents | Benchmark dataset evaluation |
| **UI & Design Integration** | **Stitch AI UI design generation integration (`/stitch`)** | CLI & messaging UI | Channel gateways | Custom web dashboard | CLI eval reports |
| **Proactive Scheduling** | Read-only Heartbeat & Reminder thread | Cron & background jobs | Scheduled monitoring | Scheduled research | Automated eval runs |

## Key Yuki Differentiators

1. **Strict Fail-Closed Approval Gate (`PermissionGate`)**:
   In Yuki, model responses are treated as untrusted text proposals. Every state-changing skill call requires fresh human approval with rate-limiting (10 approvals / 5 minutes) and a 120s timeout.

2. **Environment & Secrets Scrubbing (`core/env_guard.py`)**:
   Yuki automatically scrubs sensitive environment variables (API keys, credentials, tokens) before launching subprocesses, sandboxes, or plugins.

3. **Stitch AI UI Design Generation (`skills/stitch_skill.py`)**:
   Yuki integrates Stitch UI design generation, allowing operators to create UI design projects, design systems, and frontend screens directly from terminal prompts.
