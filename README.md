# Yuki

Yuki is an experimental modular personal AI agent for desktop and terminal interfaces (Python 3.10+, stdlib-first, MIT license). It is under active development; do not assume a feature is production-ready solely because a module or CLI command exists.

## Quickstart: launch Yuki from source

Requirements: Python 3.10 or newer. Run these commands from the repository root.

```sh
git clone https://github.com/trivenico/Yuki.git
cd Yuki
python -m venv .venv
```

Activate the environment, then install the declared runtime dependency:

```sh
# Linux/macOS
. .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1` instead. Configure a provider and launch the terminal assistant:

```sh
python -m ui.onboarding_tui
python main.py
```

On first launch, Yuki may start onboarding automatically if `~/.yuki/config.json` does not exist. In the wizard, choose Ollama for local inference and enter the model name already available in your Ollama installation. Ensure Ollama is running and the model has been downloaded before asking Yuki a question. Then type a normal prompt or use `/ask YOUR_PROMPT`; use `/help` for commands and `/doctor` for diagnostics. The setup wizard does not itself prove the endpoint or model is reachable.

To open the optional desktop window, install its dependency and launch Yuki as above, then enter `/desktop` in the terminal:

```sh
python -m pip install -r requirements-desktop.txt
python main.py
```

The project does not currently define a `pyproject.toml` package or installable `yuki` / `yuki-onboard` console commands. Use the source-tree commands in this guide; do not use `pip install -e .` or `yuki doctor` for this revision.

### First-run onboarding and interfaces

Run `python -m ui.onboarding_tui` to create a private user configuration at `~/.yuki/config.json`. On an interactive first launch, Yuki starts onboarding automatically when that file is missing. API keys are not written to the JSON file; enter credentials through environment variables named during setup. Existing `config.yaml` settings remain the base configuration, with supported user settings overlaid at startup.

- Supported provider adapters: Ollama, OpenAI-compatible endpoints (OpenAI, DeepSeek, GLM, llama.cpp, LM Studio), and Anthropic. The mock adapter remains for explicit tests only and is not offered by production onboarding.
- Hybrid routing stays local by default. Only explicitly configured cloud task names can route externally, and CLI cloud prompts require confirmation.
- Configure MCP servers in onboarding. Use `/mcp discover`, `/mcp tools`, and `/mcp invoke mcp.SERVER.TOOL | {"key":"value"}`. Discovery does not authorize execution; each call requires a fresh approval and an explicit tool allow-list.
- `/dashboard` opens the optional Textual status UI (`pip install -r requirements-ui.txt`).
- `/desktop` opens the optional PyWebView desktop shell (`pip install -r requirements-desktop.txt`). It provides plain-text provider chat and read-only runtime/skill status. Cloud prompts require a per-message confirmation; desktop chat never invokes skills, shell commands, or workspace writes.
- Token usage is stored locally under the configured workspace; provider counts are exact when supplied by the provider and otherwise marked as estimates.

These additions are an integration increment, not a claim of production readiness. Run the test suite and review provider/network policies before using real credentials or sensitive data.

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
