# Comparative Analysis: Yuki vs. Hermes, OpenClaw, OpenJarvis, and DeepSeek Harness

This document compares Yuki's architecture with prominent open-source AI agent frameworks.

## Framework Comparison Matrix

| Architectural Dimension | Yuki | Hermes Agent | OpenClaw | OpenJarvis | DeepSeek Harness |
|---|---|---|---|---|---|
| **Primary Paradigm** | Secure-by-construction local personal agent | Procedural skills & persistent CLI worker | Cross-device assistant with channel gateways | Local deep research & evaluation presetter | Benchmark & model evaluation harness |
| **Tool Execution Model** | **Fail-closed human approval for every skill call** | Autonomous tool calls with permission profiles | Gateways with scoped permissions | Scheduled & autonomous research loops | Automated benchmark evaluation harness |
| **Multi-Agent & Sub-Tasks** | **`AgentDelegator` / `/delegate` role-decomposed execution** | Multi-agent collaboration | Single node delegation | Research workflow pipelines | Single evaluator harness |
| **Channel Gateway Support** | **`ChannelGateway` / `/gateway` multi-channel routing** | CLI / API endpoints | Messaging channel gateways | Local API & web interface | Benchmark reports |
| **Deep Research Capability** | **`DeepResearchEngine` / `/research` multi-step synthesis** | External search tools | Channel web search | Deep research engine | Benchmark evaluation |
| **Model Output Trust** | Untrusted text proposals; no shell execution | Tool-calling model output | Remote & device node tool calls | Evaluated research output | Structured benchmark evaluation |
| **Local Privacy** | Loopback-HTTP / HTTPS only; no implicit cloud fallback | Model/provider abstraction | Device nodes & messaging channels | Local AI & selectable agents | Benchmark dataset evaluation |
| **UI & Design Integration** | **Stitch AI UI design generation integration (`/stitch`)** | CLI & messaging UI | Channel gateways | Custom web dashboard | CLI eval reports |
| **Proactive Scheduling** | Read-only Heartbeat & Reminder thread | Cron & background jobs | Scheduled monitoring | Scheduled research | Automated eval runs |

## Key Yuki Architecture Integrations

1. **Deep Research Synthesis Loop (`knowledge/deep_research.py`)**:
   Enables multi-step query expansion, source attribution, and structured report synthesis.

2. **Multi-Agent Sub-Task Delegation (`core/agent_delegator.py`)**:
   Decomposes goals across specialized roles (developer, security, tutor) with isolated context execution.

3. **Multi-Channel Gateway Envelope (`core/channel_gateway.py`)**:
   Standardized `ChannelMessage` envelopes routing across CLI, webhook, Slack/Discord integrations with isolated session state.

4. **Strict Fail-Closed Approval Gate (`PermissionGate`)**:
   Model responses are treated as untrusted proposals. Every state-changing skill call requires explicit human approval with rate-limiting and timeouts.
