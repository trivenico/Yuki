# Yuki

Yuki is a secure-by-construction modular personal AI agent for desktop and terminal interfaces (Python 3.10+, stdlib-first, MIT license).

## Quickstart

```sh
git clone https://github.com/your-username/yuki.git
cd yuki
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

| Feature | Readiness Status | Details |
|---|---|---|
| **Human-in-the-Loop Approvals** | **Production Ready** | Fail-closed via `PermissionGate`, rate-limiting (10/5m), 120s timeout |
| **Conversational Chat Loop** | **Production Ready** | Multi-turn via `/api/chat`, persona prompt, rolling history, memory context |
| **Bounded Task Agent** | **Production Ready** | Bounded steps (1-5), strict JSON tool proposals requiring fresh approval |
| **Provider Routing** | **Production Ready** | `mock`, `ollama` (`/api/generate` & `/api/chat`), `hybrid` local/cloud routing |
| **Environment & Secrets Guard** | **Production Ready** | Scrubbing of sensitive environment variables before subprocess/plugin execution |
| **MCP Tool Integration** | **Production Ready** | Restricted HTTPS Streamable HTTP MCP client wrapper & CLI commands |
| **Proactive Reminders & Heartbeat** | **Production Ready** | Background scheduler thread & read-only HEARTBEAT.md check runner |
| **Diagnostics & Observability** | **Production Ready** | `yuki doctor`, `/doctor`, `/diagnose` bundle generator, JSONL audit log |
| **Live Firewall Mutation** | **Dry-Run Only** | Human-approval dry-run simulation only; live rule mutations disabled |
| **Speaker Verification** | **Scaffold Only** | Fail-closed placeholder without verified speaker model |

## Position vs. Other Agents

Unlike autonomous agent frameworks that grant unmonitored shell or browser access, Yuki operates on a **secure-by-construction** paradigm:
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
