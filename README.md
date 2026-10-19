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

### First-run onboarding and interfaces

Run `yuki-onboard` (or `python -m ui.onboarding_tui`) to create a private user configuration at `~/.yuki/config.json`. On an interactive first launch, Yuki starts onboarding automatically when that file is missing. API keys are not written to the JSON file; enter credentials through environment variables named during setup. Existing `config.yaml` settings remain the base configuration, with supported user settings overlaid at startup.

- Supported provider adapters: Mock, Ollama, OpenAI-compatible endpoints (OpenAI, DeepSeek, GLM, llama.cpp, LM Studio), and Anthropic.
- Hybrid routing stays local by default. Only explicitly configured cloud task names can route externally, and CLI cloud prompts require confirmation.
- Configure MCP servers in onboarding. Use `/mcp discover`, `/mcp tools`, and `/mcp invoke mcp.SERVER.TOOL | {"key":"value"}`. Discovery does not authorize execution; each call requires a fresh approval and an explicit tool allow-list.
- `/dashboard` opens the optional Textual status UI (`pip install -r requirements-ui.txt`).
- `/desktop` opens the optional read-only PyWebView shell (`pip install -r requirements-desktop.txt`). It shares the running process state but intentionally exposes no privileged actions.
- Token usage is stored locally under the configured workspace; provider counts are exact when supplied by the provider and otherwise marked as estimates.

### Onboarding and reviewed skill proposals

- Run `python -m ui.onboarding_tui` (or `yuki-onboard`) to configure a first-run install. The main CLI launches the wizard automatically when `~/.yuki/config.json` is missing and stdin is interactive; non-interactive runs stop and tell you how to run setup explicitly.
- Configuration is validated in `core/onboarding.py`, atomically written with restrictive file permissions, and described by `schemas/user-config-v1.schema.json`. Raw API keys are not stored; use the named environment variable. Provider URL syntax is checked, but endpoint reachability/authentication is not claimed unless a provider-specific connectivity check is added.
- Generate a proposal with `/skill draft summarize user-provided notes`; inspect with `/skill proposals` and `/skill preview summarize-user-provided-notes`; approve or reject using `/skill approve NAME` or `/skill reject NAME`. Both decisions require terminal confirmation. Rejected proposals are archived.
- Approved documents are loaded by `SkillRegistry.load_approved_documents()` as **inert instruction resources**. They are not imported or compiled as Python, and their content cannot call tools. Executable Python plugin generation is intentionally not enabled by this workflow.
- The Textual dashboard can display pending proposals using `/skill proposals`; approvals stay in the terminal. Desktop wrappers use the same on-disk configuration and workspace state.

### Roadmap increment: daemon, messaging, skills and native inference

- **Async scheduler/daemon:** `automation/async_scheduler.py` stores scheduled work in SQLite, supports bounded five-field UTC cron and interval schedules, and only invokes named in-process handlers. Approval is requested when each job becomes due; headless/missing approval denies execution. Start the foreground notification daemon with `yuki-daemon --notify "stretch break" --every 3600` or `yuki-daemon --notify "weekday check-in" --cron "0 9 * * 1-5"`. Notifications are local-only. Register additional trusted handlers in application code; do not register model-generated shell/code handlers.
- **Discord/Telegram:** `integrations/messaging_bridge.py` validates Telegram webhook secrets and Discord Ed25519 interaction signatures (install `.[discord]`), restricts senders and commands, applies a sliding-window rate limit, and requires approval for non-read-only commands. The host application must expose HTTPS endpoints, configure platform secrets/public keys, and enforce infrastructure-level request limits.
- **Dynamic skills:** `skills/proposals.py` parses bounded declarative `SKILL.md` frontmatter, stores proposals separately from approved documents, offers a static-only sandbox preview, and requires explicit human approval before moving a proposal to the approved directory. It never imports or executes proposed instructions. Connecting approved documents to executable Python skills remains a separate reviewed step.
- **Native inference:** `providers/llama_cpp_python.py` supports user-provided GGUF files through optional llama.cpp Python bindings; `providers/mlx.py` supports MLX on Apple Silicon; the factory also recognizes local vLLM OpenAI-compatible endpoints. Install only the platform-appropriate optional extra, e.g. `pip install -e '.[llama-cpp]'` or `pip install -e '.[mlx]'`. Model weights are not downloaded automatically.

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
