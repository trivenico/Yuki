# Yuki

Yuki is an experimental modular personal AI agent for desktop and terminal interfaces (Python 3.10+, stdlib-first, MIT license). It is under active development; do not assume a feature is production-ready solely because a module or CLI command exists.

## Quickstart: launch Yuki from source

Requirements: Python 3.10 or newer. Run these commands from the repository root.

```powershell
git clone https://github.com/trivenico/Yuki.git
cd Yuki
# PR #349 is still an unmerged draft; fetch its candidate integration tree for testing:
git fetch origin pull/349/head:pr-349
git switch pr-349
python -m venv .venv
```

The commands above check out the PR #349 candidate, which includes its prerequisite PR branches in its history. This is for local evaluation only; it is not a released or production-certified version. If the PR has already been merged when you read this, use the default branch instead.

Activate the environment and install Yuki as a package using its `pyproject.toml`:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

On Linux/macOS, activate with `source .venv/bin/activate` instead.

#### Windows: run the real local Ollama model

1. Install Ollama for Windows from the [official download page](https://ollama.com/download/windows). It runs natively and serves its local API on `http://localhost:11434`.
2. Open a new PowerShell terminal and verify installation:

```powershell
ollama --version
ollama pull qwen2.5:3b
ollama list
ollama run qwen2.5:3b
```

The 3B model is a lighter starting point than 7B; actual speed and memory requirements depend on your RAM/GPU. Exit the Ollama chat with `/bye` after confirming it answers.

3. In the activated Yuki virtual environment, configure Yuki:

```powershell
python -m ui.onboarding_tui
```

Choose `ollama`, keep the URL as `http://127.0.0.1:11434`, and set the model name to exactly `qwen2.5:3b`. Do not configure a cloud provider or API key for this local-only path.

4. Run the actual-provider smoke test, then launch Yuki:

```powershell
python scripts/smoke_test_ollama.py
python main.py
```

The smoke test checks that the configured local endpoint lists the chosen model, then sends one short prompt through Yuki's `YukiAgent` and verifies that a real response contains the expected marker. It exits nonzero on setup, connectivity, model, or inference failure. A passing smoke test is evidence of local inference, not a certification of all Yuki features.

At the Yuki prompt, type a normal message or use `/ask Explain what you can help me with`; use `/help` for commands and `/doctor` for diagnostics. The onboarding wizard validates endpoint syntax but does not itself verify that Ollama or the model is reachable.

To open the optional desktop window, install its dependency and launch Yuki as above, then enter `/desktop` in the terminal:

```sh
python -m pip install -r requirements-desktop.txt
python main.py
```

The project defines `yuki` and `yuki-onboard` console entry points in `pyproject.toml`. For this smoke-test workflow, use `python main.py` and `python -m ui.onboarding_tui` so the selected checkout is explicit. Use `/doctor` inside Yuki for diagnostics; `yuki doctor` is not a documented subcommand.

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
