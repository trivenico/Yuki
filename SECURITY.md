# Security Policy, Scope & Threat Model

Yuki is a modular personal AI agent designed with fail-closed security architectures.

## Threat Model & Trust Boundaries

1. **Model Output**: Untrusted data. Provider answers cannot directly run shell commands or execute arbitrary code. Tool calls are proposals requiring human approval.
2. **External Content / Prompt Injection**: Web pages, MCP tool results, and indexable documents are untrusted. Scanned for heuristic injection signals.
3. **Subprocess & Environment Egress**: Subprocess calls and plugin executions receive scrubbed environment variables (`core/env_guard.py`) stripping keys and credentials.
4. **Plugin Code**: Plugin discovery is disabled by default. Code is scanned for high-risk patterns (`os.system`, `subprocess(shell=True)`, `eval/exec`) prior to import.
5. **Secret & Memory Files**: File permissions on secret tokens and audit logs are restricted (chmod 0600 / 0640) on POSIX platforms.

## Guardrails & Non-Negotiables

- Fail-closed human approval via `PermissionGate` for every skill call.
- Bounded steps (1-5), memory entries (<= 2000), reminders (<= 1000).
- Local-first privacy: Loopback HTTP or HTTPS only for Ollama endpoints.

## Known Limitations

- Live firewall rule mutation is dry-run only.
- Speaker verification is a fail-closed scaffold.
- Local voice models and hardware dependencies require separate installation.

## Reporting Issues

Do not publish secrets or sensitive exploit details in public issues. Report security concerns to the repository maintainer.
