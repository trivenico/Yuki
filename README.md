# Yuki

Yuki is an early-stage personal AI-agent project aiming to unite a helpful **personal companion**, a **developer agent**, and a **defensive cybersecurity assistant** behind one provider-independent architecture.

The ambition is to learn from the best publicly observable ideas in projects such as Hermes, OpenClaw, Moltbot, AutoClaw, and OpenJarvis while building Yuki's own reliable, privacy-aware product. This is a roadmap, not a claim of feature parity today.

## Current implementation status

The current Python prototype includes:
- A terminal command loop
- A provider interface and an offline mock provider
- An explicit allow-list for basic local tools
- Local task, memory, and outcome-learning records
- A default-deny tool-name policy at the command boundary
- A local metadata-only audit log for selected actions
- Unit tests and a GitHub Actions workflow

**The mock provider does not call an AI service.** OpenAI, Anthropic, Gemini, and local-runtime entries are registry metadata only. No real model integrations, provider login flows, browser automation, autonomous coding, or cybersecurity scanning engine are implemented yet. No API key is required to develop or test the current foundation.

## Requirements

- Python 3.10 or newer
- No third-party packages required

## Run

From the repository directory:

```bash
python yuki.py
```

Useful commands:
- `/help`
- `/status`
- `/providers`
- `/tool calculate 12 * (3 + 4)`
- `/task add Test Yuki`
- `/task list`
- `/memory add My first note`
- `/learn Organize files | success | Group related files before reviewing them`
- `/lessons Organize files`
- `/audit`
- `/quit`

Run tests locally with:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions runs the test suite on pushes and pull requests.

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

## Development sequence

See [ROADMAP.md](ROADMAP.md) for the staged engineering plan and [SECURITY.md](SECURITY.md) for security boundaries.

Yuki is not production-ready yet. The goal is to earn trust through tests, threat modelling, transparent permissions, and verified capabilities before enabling more autonomy.
