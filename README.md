# Yuki

Yuki is an early-stage personal AI-agent project aiming to unite a helpful **personal companion**, a **developer agent**, and a **defensive cybersecurity assistant** behind one provider-independent architecture.

The ambition is to learn from the best publicly observable ideas in projects such as Hermes, OpenClaw, Moltbot, AutoClaw, and OpenJarvis while building Yuki's own reliable, privacy-aware product. This is a roadmap, not a claim of feature parity today.

## Current implementation status

The repository now has two Python entry points:
- `main.py`: new guarded core runtime connecting YAML configuration, Yuki persona, registered read-only skills, and explicit per-action approval.
- `yuki.py`: existing feature-rich prototype with tasks, local notes, outcome-learning records, mock provider, allow-listed tools, and metadata-only audit events.

The shared foundations also include a provider interface, bounded planning and workflow models, an event bus, a task-preparation orchestrator, an approval gate, and unit tests. The guarded runtime currently supports read-only `time`, `system`, and workspace-only `files` skills. Approval is denied by default if the user does not explicitly answer yes.

**The mock provider does not call an AI service.** OpenAI, Anthropic, Gemini, and local-runtime entries in the legacy provider registry are metadata only. No real model integrations, provider login flows, browser automation, autonomous coding, microphone, speech recognition, text-to-speech, graphical permission dashboard, or cybersecurity scanning engine are implemented yet. The new core runtime is a safe integration seam, not an LLM-driven ReAct loop.

## Voice identity

Yuki's only intended wake word is **“Yuki”** — not “Hey Yuki”. Voice activation is disabled by default. The intended greeting is “Hai, Master. I'm here. How may I help you?” Microphone access, wake-word detection, speech recognition, and speech output are not implemented yet. Text and CLI remain the current interfaces.

## Requirements

- Python 3.10 or newer
- PyYAML (installed from `requirements.txt` for the new YAML config loader)
- No AI API key is required for the current mock-provider prototypes

## Installation and first run

See [the installation guide](docs/INSTALLATION.md) and [project structure](docs/PROJECT_STRUCTURE.md).

- **Windows:** run `install_windows.bat`, then `run_yuki_windows.bat` for the existing terminal prototype.
- **macOS/Linux:** run `sh install_unix.sh`, then `sh run_yuki_unix.sh`.
- **Guarded core:** after dependencies are installed, run `python main.py`.
- **Existing prototype:** run `python yuki.py`.

The setup scripts create an isolated `.venv`, install `requirements.txt`, and run the test suite. No model API key is needed. A successful install is not a claim that voice, live AI, browser automation, or autonomous coding is ready.

## Guarded core commands

Run `python main.py` and use:
- `/help`
- `/status`
- `/skills`
- `/skill time`
- `/skill system`
- `/skill files .`
- `/quit`

Each skill request asks for approval; only a literal `y` approves that one action. File skill only lists entries within the configured workspace and never reads file contents. Set `YUKI_WORKSPACE` to override the workspace. See `config.yaml` and `.env.example` for non-secret configuration examples. The current code does not automatically load a `.env` file; environment variables must be set by the shell or launch environment.

## Existing prototype commands

Run `python yuki.py` and use `/help`, `/status`, `/providers`, `/tool calculate 12 * (3 + 4)`, `/task add Test Yuki`, `/task list`, `/memory add My first note`, `/learn Organize files | success | Group related files before reviewing them`, `/lessons Organize files`, `/audit`, and `/quit`.

Run diagnostics with `python scripts/doctor.py`. Run tests locally with:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

GitHub Actions runs tests on pushes and pull requests.

## Product tracks

1. **Companion:** user-controlled memory, conversational continuity, adjustable personality, privacy and deletion controls.
2. **Developer:** repository understanding, code navigation, test-driven changes, sandboxed execution, reviewable diffs, and rollback.
3. **Defensive cybersecurity:** authorized asset inventory, secure configuration checks, dependency and secret scanning, findings with evidence, and remediation guidance. Security actions must be scoped and authorized.

## Production principles

- Default-deny for tools; never expose arbitrary shell or Python execution to the model by default.
- Require explicit approval for consequential actions such as publishing changes, deleting data, accessing sensitive resources, or changing security settings.
- Treat webpage, repository, and file contents as untrusted input; do not let prompt injection override system policy.
- Store credentials only through a dedicated secure mechanism when real provider adapters are implemented. Never commit keys or tokens.
- Keep audit records useful but minimal; current audit events intentionally omit prompts and tool arguments.
- Local memory and task files are user data. They are ignored by Git and must remain under user control.
- Learning history retrieves lessons from outcomes; it does not silently train or rewrite the model.
- The `hackback/` directory documents a safe, authorized Security Lab Engine boundary only. No hack-back functionality is implemented.

## Development sequence

See [ROADMAP.md](ROADMAP.md) for the staged engineering plan and [SECURITY.md](SECURITY.md) for security boundaries.

Yuki is not production-ready yet. The goal is to earn trust through tests, threat modelling, transparent permissions, and verified capabilities before enabling more autonomy.
