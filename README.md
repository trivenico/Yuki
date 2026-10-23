# Yuki

Yuki is an experimental modular personal AI agent for desktop and terminal interfaces (Python 3.10+, stdlib-first, MIT license). It is under active development; do not assume a feature is production-ready solely because a module or CLI command exists.

## Quickstart

```sh
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python -m pip install -e .
yuki doctor
yuki
```

To run unit tests and evaluation suite:

```sh
python -m unittest discover -s tests -v
python evals/run_evals.py
```

## Capability Readiness Matrix

| Feature | Implementation / verification status | Details |
|---|---|---|
| **Human-in-the-Loop Approvals** | **Verification pending** | Fail-closed via `PermissionGate`, rate-limiting (10/5m), 120s timeout |
| **Conversational Chat Loop** | **Verification pending** | Multi-turn via `/api/chat`, persona prompt, rolling history, memory context |
| **Bounded Task Agent** | **Verification pending** | Bounded steps (1-5), strict JSON tool proposals requiring fresh approval |
| **Provider Routing** | **Verification pending** | `mock`, `ollama` (`/api/generate` & `/api/chat`), `hybrid` local/cloud routing |
| **Environment & Secrets Guard** | **Verification pending** | Scrubbing of sensitive environment variables before subprocess/plugin execution |
| **MCP Tool Integration** | **Verification pending** | Restricted HTTPS Streamable HTTP MCP client wrapper & CLI commands |
| **Proactive Reminders & Heartbeat** | **Verification pending** | Background scheduler thread & read-only HEARTBEAT.md check runner |
| **Diagnostics & Observability** | **Verification pending** | `yuki doctor`, `/doctor`, `/diagnose` bundle generator, JSONL audit log |
| **Live Firewall Mutation** | **Dry-Run Only** | Human-approval dry-run simulation only; live rule mutations disabled |
| **Speaker Verification** | **Scaffold Only** | Fail-closed placeholder without verified speaker model |

## Security model and limitations

Yuki is designed to prefer local execution and human approval, but security depends on the exact code path, configuration, provider, and platform. Treat the project as **not production-ready until the required CI matrix, security review, integration tests, and release gate pass**. Never expose it to untrusted users or sensitive production workloads based only on the statements below.

Unlike autonomous agent frameworks that grant unmonitored shell or browser access, Yuki aims to enforce these boundaries:
- Model output is treated as untrusted data.
- Every consequential skill call requires explicit, fresh human approval.
- No implicit cloud fallbacks; local privacy by default.

## Documentation

- [Architecture & Module Map](docs/architecture.md)
- [Security & Approval Flow](docs/security.md)
- [Data Storage & Deletion](docs/data.md)
- [Provider API Contract](docs/api.md)
- [Upgrade & Migration Guide](UPGRADE.md)
- [Security Policy](SECURITY.md)
